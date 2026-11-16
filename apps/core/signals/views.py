"""`/v1/signals` (an account's signals: a sheet of kind log fed by its
sources), and what the Create-signal screen reads: `/v1/signal-types`
(the catalog and a type's recent events) and `/v1/signal-previews` (a
setup tried before it is saved). Session-authed; every signals call acts
as the person, and every failure answers through one mapping so a
refusal reads the same everywhere."""

from __future__ import annotations

import datetime
import logging
from functools import cached_property

from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from auth_client.authentication import AppSessionAuthentication
from auth_client.constants import ApiKeyRefusalCode, AuthErrorCode
from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.api_keys import ApiKeyRefused
from auth_client.services.oauth import AuthUpstreamError, AuthUpstreamUnavailable
from common.views import ScopedView
from openbower_kernel.pagination import next_cursor_from, parse_limit
from openbower_schema import SignalDetail, SignalsPage, SignalSummary, SourceSummary, SyncKeyReconnected
from resource_server import MachineTokenAuthentication
from sheets.constants import DEFAULT_INDEX_PAGE, LABEL_MAX_LENGTH, MAX_INDEX_PAGE, IngestErrorCode, RowSource
from sheets.ingest.accept import PushRefused, accept_push
from sheets.serializers import sheet_summary
from sheets.services.columns import ColumnRefused

from .constants import KEY_LIMIT_DETAIL, PREVIEW_RUNNING_DETAIL, SignalsErrorCode
from .operations.logs import (
    CreateCustomSignalOperation,
    CreateLogOperation,
    DeleteLogOperation,
    EditLogOperation,
    ListenerGone,
    Log,
    ReadLogOperation,
    ReconnectLogsOperation,
    SetupOnCustomSignal,
    UnknownSignal,
)
from .serializers import LogCreateRequest, LogPatchRequest, PreviewCreateRequest
from .services.logs import LocalLog, LogNotFound, LogService
from .services.signals_client import (
    SignalsAccessDenied,
    SignalsClientRequestError,
    SignalsClientService,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
    setup_payload,
)
from .sources.registry import WEBHOOK
from .sources.webhook import WebhookSource

logger = logging.getLogger(__name__)


class _Relayed(APIException):
    """A refusal answered as its own body and status (the signals service's
    400/404/409, the key-limit copy), past DRF's detail wrapping."""

    def __init__(self, body: dict, status: int) -> None:
        super().__init__()
        self.detail = body
        self.status_code = status


def _as_api_error(exc: Exception) -> Exception:
    """A signals or identity failure as the error the API answers with:
    the signals service's own 400/404/409 verbatim, a session it no longer
    honors as 401 (sign in again), a missing scope as 403 (sign in again),
    the sync key's key-limit refusal in the app's words, and a service that
    is down or answering nonsense as 503, naming which: the identity
    service (the sync key's mint) or the signals service. Anything else is
    left as raised."""
    if isinstance(exc, LogNotFound):
        return NotFound("no signal with that id")
    if isinstance(exc, ListenerGone):
        return _Relayed({"error": SignalsErrorCode.LISTENER_GONE, "detail": str(exc)}, status=409)
    if isinstance(exc, UnknownSignal):
        return _Relayed({"error": SignalsErrorCode.UNKNOWN_SIGNAL, "detail": str(exc)}, status=400)
    if isinstance(exc, SetupOnCustomSignal):
        return _Relayed({"error": SignalsErrorCode.SETUP_ON_CUSTOM_SIGNAL, "detail": str(exc)}, status=400)
    if isinstance(exc, ColumnRefused):
        # A custom signal's declared columns: the request's own labels
        # collide or reserve a key, so it is the request to fix.
        return _Relayed({"error": exc.code, "detail": str(exc)}, status=400)
    if isinstance(exc, SignalsPreviewRunning):
        return _Relayed({**exc.body, "detail": PREVIEW_RUNNING_DETAIL}, status=exc.status)
    if isinstance(exc, SignalsClientRequestError):
        return _Relayed(exc.body, status=exc.status)
    if isinstance(exc, ApiKeyRefused):
        if exc.error == ApiKeyRefusalCode.KEY_LIMIT_REACHED:
            return _Relayed({"error": SignalsErrorCode.KEY_LIMIT_REACHED, "detail": KEY_LIMIT_DETAIL}, status=409)
        return _Relayed({"error": exc.error, "detail": exc.detail}, status=exc.status)
    if isinstance(exc, DownstreamTokenRejected):
        return AuthenticationFailed("session expired or revoked")
    if isinstance(exc, SignalsAccessDenied):
        return PermissionDenied("sign in again to use signals")
    if isinstance(exc, AuthUpstreamUnavailable | AuthUpstreamError):
        logger.warning("signals: identity service unavailable (%s)", type(exc).__name__)
        body = {"error": AuthErrorCode.IDENTITY_UNAVAILABLE, "detail": "identity service unavailable"}
        return _Relayed(body, status=503)
    if isinstance(exc, SignalsUpstreamError):
        unavailable = isinstance(exc, SignalsUpstreamUnavailable)
        logger.warning("signals: upstream %s (%s)", "unavailable" if unavailable else "error", type(exc).__name__)
        body = {"error": SignalsErrorCode.SIGNALS_UNAVAILABLE, "detail": "the signals service is unavailable; retry"}
        return _Relayed(body, status=503)
    return exc


def _sources(local_log: LocalLog) -> list[SourceSummary]:
    return [source.config.summary(source) for source in local_log.sources]


def _last_polled(local_log: LocalLog) -> str | None:
    """The latest read through by any of the log's polled sources."""
    reads = [source.last_polled_at for source in local_log.sources if source.last_polled_at is not None]
    return _iso(max(reads, default=None))


def _summary(local_log: LocalLog) -> SignalSummary:
    sheet = sheet_summary(local_log.sheet)
    return SignalSummary(
        sheet=sheet,
        sources=_sources(local_log),
        status=local_log.status,
        last_polled_at=_last_polled(local_log),
        last_received_at=_iso(local_log.last_received_at),
    )


def _detail(log: Log) -> SignalDetail:
    sheet = sheet_summary(log.local_log.sheet)
    return SignalDetail(
        sheet=sheet,
        sources=_sources(log.local_log),
        listener=log.listener,
        status=log.local_log.status,
        last_polled_at=_last_polled(log.local_log),
        last_received_at=_iso(log.local_log.last_received_at),
    )


def _iso(at: datetime.datetime | None) -> str | None:
    return None if at is None else at.isoformat()


class _SignalsScopedView(ScopedView):
    """Every signals and identity failure answers through `_as_api_error`,
    so a refusal reads the same on every route."""

    def handle_exception(self, exc: Exception) -> Response:
        return super().handle_exception(_as_api_error(exc))

    @cached_property
    def logs(self) -> LogService:
        return LogService(account_id=self.request.user.account_id)

    @cached_property
    def signals(self):
        return SignalsClientService.for_session(self.request.auth)


class SignalsView(_SignalsScopedView):
    def get(self, request: Request) -> Response:
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        logs = self.logs.page(after_id=request.query_params.get("after", ""), limit=limit)
        items = [_summary(log) for log in logs]
        next_cursor = next_cursor_from(logs, limit=limit, cursor=lambda log: log.sheet_id)
        page = SignalsPage(items=items, next_cursor=next_cursor)
        return Response(page.model_dump())

    def post(self, request: Request) -> Response:
        body = LogCreateRequest(data=request.data)
        body.is_valid(raise_exception=True)
        fields = dict(body.validated_data)
        if fields.pop("source") == WEBHOOK:
            operation = CreateCustomSignalOperation(request.auth, **fields)
        else:
            operation = CreateLogOperation(request.auth, **fields)
        log = operation.run()
        detail = _detail(log)
        return Response(detail.model_dump(), status=201)


class SignalIngestView(_SignalsScopedView):
    """POST /v1/signals/{id}/ingest: rows pushed to a custom signal, the
    sheets push endpoint's envelope and answers (a 202 receipt; the rows
    land off the bus), pushed with an account API key or a session. A
    signal with no webhook source takes no push."""

    authentication_classes = [AppSessionAuthentication, MachineTokenAuthentication]

    def post(self, request: Request, id: str) -> Response:
        local_log = self.logs.get(id)
        if local_log.find(WebhookSource) is None:
            detail = "this signal takes no pushes; only a custom signal does"
            return Response({"error": IngestErrorCode.NOT_APPENDABLE, "detail": detail}, status=409)
        user = request.user
        try:
            accepted = accept_push(
                local_log.sheet, request.data, account_id=user.account_id, user_id=user.id, source=RowSource.SIGNAL
            )
        except PushRefused as e:
            return Response(e.body, status=e.status)
        return Response(accepted.model_dump(), status=202)


class SignalsReconnectView(_SignalsScopedView):
    def post(self, request: Request) -> Response:
        replaced = ReconnectLogsOperation(request.auth).run()
        body = SyncKeyReconnected(
            left_live_key_id=replaced.left_live_key_id, left_live_user_id=replaced.left_live_user_id
        )
        return Response(body.model_dump())


class SignalDetailView(_SignalsScopedView):
    def get(self, request: Request, id: str) -> Response:
        log = ReadLogOperation(request.auth, id).run()
        detail = _detail(log)
        return Response(detail.model_dump())

    def patch(self, request: Request, id: str) -> Response:
        body = LogPatchRequest(data=request.data)
        body.is_valid(raise_exception=True)
        log = EditLogOperation(request.auth, id, **body.validated_data).run()
        detail = _detail(log)
        return Response(detail.model_dump())

    def delete(self, request: Request, id: str) -> Response:
        DeleteLogOperation(request.auth, id).run()
        return Response(status=204)


class SignalTypesView(_SignalsScopedView):
    def get(self, request: Request) -> Response:
        catalog = self.signals.catalog()
        # A signal's name is its sheet's label too, so the limit a form
        # holds it to is the tighter of the two.
        limits = catalog.limits
        limits.label_max_length = min(limits.label_max_length, LABEL_MAX_LENGTH)
        return Response(catalog.model_dump())


class SignalTypeEventsView(_SignalsScopedView):
    def get(self, request: Request, signal: str) -> Response:
        after = request.query_params.get("after") or None
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        page = self.signals.signal_events(signal=signal, after=after, limit=limit)
        return Response(page.model_dump())


class SignalPreviewsView(_SignalsScopedView):
    def post(self, request: Request) -> Response:
        body = PreviewCreateRequest(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        setup = setup_payload(
            filter=data["filter"], prompt=data.get("prompt"), min_confidence=data.get("min_confidence")
        )
        payload = {"signal": data["signal"], "event_ids": data["event_ids"], **setup}
        preview = self.signals.create_preview(payload=payload)
        return Response(preview.model_dump(), status=202)


class SignalPreviewView(_SignalsScopedView):
    def get(self, request: Request, id: str) -> Response:
        preview = self.signals.preview(preview_id=id)
        return Response(preview.model_dump())

    def delete(self, request: Request, id: str) -> Response:
        preview = self.signals.cancel_preview(preview_id=id)
        return Response(preview.model_dump())

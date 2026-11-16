"""`/v1/logs` (an account's logs: a sheet fed by a hosted signal's listener
or by a custom signal's webhook) and
`/v1/signals` (the catalog, recent events and previews the Create-log
screen reads). Session-authed; every signals call acts as the person, and
every failure answers through one mapping so a refusal reads the same everywhere."""

from __future__ import annotations

import datetime
import logging
from functools import cached_property

from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from auth_client.constants import ApiKeyRefusalCode, AuthErrorCode
from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.api_keys import ApiKeyRefused
from auth_client.services.oauth import AuthUpstreamError, AuthUpstreamUnavailable
from common.views import ScopedView
from openbower_kernel.pagination import next_cursor_from, parse_limit
from openbower_schema import LogDetail, LogsPage, LogSummary, SyncKeyReconnected
from sheets.constants import DEFAULT_INDEX_PAGE, LABEL_MAX_LENGTH, MAX_INDEX_PAGE
from sheets.serializers import sheet_summary
from sheets.services.columns import ColumnRefused

from .constants import KEY_LIMIT_DETAIL, PREVIEW_RUNNING_DETAIL, LogSource, SignalsErrorCode
from .operations.logs import (
    CreateLogOperation,
    CreateWebhookLogOperation,
    DeleteLogOperation,
    EditLogOperation,
    ListenerGone,
    Log,
    ReadLogOperation,
    ReconnectLogsOperation,
    SetupOnWebhookLog,
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
        return NotFound("no log with that id")
    if isinstance(exc, ListenerGone):
        return _Relayed({"error": SignalsErrorCode.LISTENER_GONE, "detail": str(exc)}, status=409)
    if isinstance(exc, UnknownSignal):
        return _Relayed({"error": SignalsErrorCode.UNKNOWN_SIGNAL, "detail": str(exc)}, status=400)
    if isinstance(exc, SetupOnWebhookLog):
        return _Relayed({"error": SignalsErrorCode.SETUP_ON_WEBHOOK, "detail": str(exc)}, status=400)
    if isinstance(exc, ColumnRefused):
        # A custom signal's declared columns: the request's own labels
        # collide or reserve, so it is the request to fix.
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


def _summary(local_log: LocalLog, *, last_received_at: datetime.datetime | None) -> LogSummary:
    sheet = sheet_summary(local_log.sheet)
    feed = local_log.feed
    return LogSummary(
        sheet=sheet,
        source=feed.source,
        listener_id=feed.listener_id or None,
        signal=feed.signal or None,
        status=local_log.status,
        last_polled_at=_iso(feed.last_polled_at),
        last_received_at=_iso(last_received_at),
    )


def _detail(log: Log, *, last_received_at: datetime.datetime | None) -> LogDetail:
    sheet = sheet_summary(log.local_log.sheet)
    feed = log.local_log.feed
    return LogDetail(
        sheet=sheet,
        source=feed.source,
        listener=log.listener,
        signal=feed.signal or None,
        status=log.local_log.status,
        last_polled_at=_iso(feed.last_polled_at),
        last_received_at=_iso(last_received_at),
    )


def _iso(at: datetime.datetime | None) -> str | None:
    return None if at is None else at.isoformat()


class _SignalsView(ScopedView):
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

    def _detail_body(self, log: Log) -> dict:
        arrivals = self.logs.last_arrivals([log.local_log])
        detail = _detail(log, last_received_at=arrivals.get(log.local_log.sheet_id))
        return detail.model_dump()


class LogsView(_SignalsView):
    def get(self, request: Request) -> Response:
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        logs = self.logs.page(after_id=request.query_params.get("after", ""), limit=limit)
        arrivals = self.logs.last_arrivals(logs)
        items = [_summary(log, last_received_at=arrivals.get(log.sheet_id)) for log in logs]
        next_cursor = next_cursor_from(logs, limit=limit, cursor=lambda log: log.sheet_id)
        page = LogsPage(items=items, next_cursor=next_cursor)
        return Response(page.model_dump())

    def post(self, request: Request) -> Response:
        body = LogCreateRequest(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        if data.pop("source") == LogSource.WEBHOOK:
            log = CreateWebhookLogOperation(request.auth, **data).run()
        else:
            log = CreateLogOperation(request.auth, **data).run()
        return Response(self._detail_body(log), status=201)


class LogsReconnectView(_SignalsView):
    def post(self, request: Request) -> Response:
        replaced = ReconnectLogsOperation(request.auth).run()
        body = SyncKeyReconnected(
            left_live_key_id=replaced.left_live_key_id, left_live_user_id=replaced.left_live_user_id
        )
        return Response(body.model_dump())


class LogDetailView(_SignalsView):
    def get(self, request: Request, id: str) -> Response:
        log = ReadLogOperation(request.auth, id).run()
        return Response(self._detail_body(log))

    def patch(self, request: Request, id: str) -> Response:
        body = LogPatchRequest(data=request.data)
        body.is_valid(raise_exception=True)
        log = EditLogOperation(request.auth, id, **body.validated_data).run()
        return Response(self._detail_body(log))

    def delete(self, request: Request, id: str) -> Response:
        DeleteLogOperation(request.auth, id).run()
        return Response(status=204)


class SignalsCatalogView(_SignalsView):
    def get(self, request: Request) -> Response:
        catalog = self.signals.catalog()
        # A signal's name is its sheet's label too, so the limit a form
        # holds it to is the tighter of the two.
        limits = catalog.limits
        limits.label_max_length = min(limits.label_max_length, LABEL_MAX_LENGTH)
        return Response(catalog.model_dump())


class SignalEventsView(_SignalsView):
    def get(self, request: Request, signal: str) -> Response:
        after = request.query_params.get("after") or None
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        page = self.signals.signal_events(signal=signal, after=after, limit=limit)
        return Response(page.model_dump())


class PreviewsView(_SignalsView):
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


class PreviewDetailView(_SignalsView):
    def get(self, request: Request, id: str) -> Response:
        preview = self.signals.preview(preview_id=id)
        return Response(preview.model_dump())

    def delete(self, request: Request, id: str) -> Response:
        preview = self.signals.cancel_preview(preview_id=id)
        return Response(preview.model_dump())

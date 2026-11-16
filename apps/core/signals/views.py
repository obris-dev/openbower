"""`/v1/signals` (an account's signals: a sheet of kind log fed by its
sources; a custom signal's push at `/v1/signals/{id}/ingest`, its webhook
URL at `/v1/signals/{id}/ingest/{token}` and that URL's rotate), and what
the Create-signal screen reads: `/v1/signal-types` (the catalog and a
type's recent events) and `/v1/signal-previews` (a setup tried before it
is saved). Session-authed, but for the push, which an account API key may
also make, and the webhook URL, whose token is its only credential; every
signals call acts as a person, and every failure answers through one
mapping so a refusal reads the same everywhere."""

from __future__ import annotations

import datetime
import logging
from functools import cached_property

from django.conf import settings
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
from openbower_schema import (
    CUSTOM_SIGNAL_TYPE,
    CustomSignalCreated,
    SignalDetail,
    SignalsPage,
    SignalSummary,
    SourceSummary,
    SyncKeyReconnected,
)
from resource_server import MachineTokenAuthentication
from sheets.column_refusals import column_refusal_status
from sheets.constants import DEFAULT_INDEX_PAGE, LABEL_MAX_LENGTH, MAX_INDEX_PAGE, IngestErrorCode, RowSource
from sheets.ingest.accept import accept_push
from sheets.ingest.refusals import PUSH_REFUSALS, push_refusal
from sheets.models import Sheet
from sheets.serializers import IngestRequest, ingest_schema_wire, sheet_summary
from sheets.services.columns import ColumnRefused

from .authentication import WebhookTokenAuthentication
from .constants import KEY_LIMIT_DETAIL, PREVIEW_RUNNING_DETAIL, SignalsErrorCode
from .operations.logs import (
    CreateCustomSignalOperation,
    CreateLogOperation,
    DeleteLogOperation,
    EditLogOperation,
    IssuedWebhook,
    ListenerGone,
    Log,
    NoWebhook,
    ReadLogOperation,
    ReconnectLogsOperation,
    RotateWebhookOperation,
    SetupOnCustomSignal,
    UnknownSignal,
    require_hosted_type,
)
from .serializers import LogCreateRequest, LogPatchRequest, PreviewCreateRequest
from .services.logs import LocalLog, LogNotFound, LogService, SignalTakesNoPush, signal_type_of
from .services.signals_client import (
    SignalsAccessDenied,
    SignalsClientRequestError,
    SignalsClientService,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
    preview_payload,
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
        return NotFound("no signal with that id")
    if isinstance(exc, ListenerGone):
        return _Relayed({"error": SignalsErrorCode.LISTENER_GONE, "detail": str(exc)}, status=409)
    if isinstance(exc, UnknownSignal):
        return _Relayed({"error": SignalsErrorCode.UNKNOWN_SIGNAL, "detail": str(exc)}, status=400)
    if isinstance(exc, SignalTakesNoPush):
        return _Relayed({"error": IngestErrorCode.NOT_APPENDABLE, "detail": str(exc)}, status=409)
    if isinstance(exc, NoWebhook):
        return _Relayed({"error": SignalsErrorCode.NO_WEBHOOK, "detail": str(exc)}, status=409)
    if isinstance(exc, SetupOnCustomSignal):
        return _Relayed({"error": SignalsErrorCode.SETUP_ON_CUSTOM_SIGNAL, "detail": str(exc)}, status=409)
    if isinstance(exc, ColumnRefused):
        # A custom signal's declared columns, classified as every column
        # write is.
        return _Relayed({"error": exc.code, "detail": str(exc)}, status=column_refusal_status(exc))
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
    latest = max(reads, default=None)
    return _iso(latest)


def _summary(local_log: LocalLog) -> SignalSummary:
    sheet = sheet_summary(local_log.sheet)
    return SignalSummary(
        sheet=sheet,
        type=signal_type_of(local_log),
        sources=_sources(local_log),
        status=local_log.status,
        last_polled_at=_last_polled(local_log),
    )


def _detail(log: Log) -> SignalDetail:
    sheet = sheet_summary(log.local_log.sheet)
    return SignalDetail(
        sheet=sheet,
        type=signal_type_of(log.local_log),
        sources=_sources(log.local_log),
        listener=log.listener,
        status=log.local_log.status,
        last_polled_at=_last_polled(log.local_log),
    )


def _iso(at: datetime.datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def _webhook_url(sheet_id: str, token: str) -> str:
    """The URL a sender is given. Spelled out: the routes' optional-slash
    include reverses without the slash after the prefix."""
    return f"{settings.BASE_URL}/{settings.API_VERSION_PREFIX}/signals/{sheet_id}/ingest/{token}"


def _issued(issued: IssuedWebhook) -> CustomSignalCreated:
    """The signal's detail and the one reading of its webhook URL."""
    sheet_id = issued.log.local_log.sheet_id
    url = _webhook_url(sheet_id, issued.token)
    detail = _detail(issued.log)
    fields = detail.model_dump()
    return CustomSignalCreated(**fields, webhook_url=url)


def _accept_push(sheet: Sheet, request: Request) -> Response:
    """A push to a custom signal, by whichever lane let it in: the sheets
    push envelope validated, accepted onto the bus as the request's user,
    and answered with the receipt or the push's refusal. Every push route
    calls this, so a step a push takes is taken on each."""
    body = IngestRequest(data=request.data)
    body.is_valid(raise_exception=True)
    user = request.user
    try:
        accepted = accept_push(
            sheet,
            body.validated_data["rows"],
            event_id=body.validated_data.get("event_id"),
            account_id=user.account_id,
            user_id=user.id,
            source=RowSource.SIGNAL,
        )
    except PUSH_REFUSALS as e:
        return push_refusal(e)
    return Response(accepted.model_dump(), status=202)


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
        signal_type = fields.pop("type")
        if signal_type == CUSTOM_SIGNAL_TYPE:
            issued = CreateCustomSignalOperation(request.auth, **fields).run()
            created = _issued(issued)
            return Response(created.model_dump(), status=201)
        log = CreateLogOperation(request.auth, signal_type=signal_type, **fields).run()
        detail = _detail(log)
        return Response(detail.model_dump(), status=201)


class SignalIngestView(_SignalsScopedView):
    """POST /v1/signals/{id}/ingest: rows pushed to a custom signal, the
    sheets push endpoint's envelope and answers (a 202 receipt; the rows
    land off the bus), pushed with an account API key or a session. A
    signal with no webhook source takes no push."""

    authentication_classes = [AppSessionAuthentication, MachineTokenAuthentication]

    def get(self, request: Request, id: str) -> Response:
        """GET /v1/signals/{id}/ingest: the pushable row schema, the
        columns a sender owns with their keys and types (the sheets push
        endpoint's own), so a push can be built without guessing."""
        sheet = self.logs.push_target(id)
        return Response(ingest_schema_wire(sheet))

    def post(self, request: Request, id: str) -> Response:
        sheet = self.logs.push_target(id)
        return _accept_push(sheet, request)


class SignalWebhookIngestView(_SignalsScopedView):
    """POST /v1/signals/{id}/ingest/{token}: the webhook URL a sender is
    given. The same envelope, answers and refusals as /ingest, with no
    session and no API key: the token in the path is the credential
    (WebhookTokenAuthentication), and the push is the signal's creator's.
    Resolved in one read, so a push touches only the database and the bus."""

    authentication_classes = [WebhookTokenAuthentication]

    def get(self, request: Request, id: str, token: str) -> Response:
        """The pushable row schema, as GET /ingest serves it."""
        grant = request.auth
        return Response(ingest_schema_wire(grant.sheet))

    def post(self, request: Request, id: str, token: str) -> Response:
        grant = request.auth
        return _accept_push(grant.sheet, request)


class SignalWebhookRotateView(_SignalsScopedView):
    """POST /v1/signals/{id}/webhook/rotate: a new webhook URL for a custom
    signal, shown once; the old one stops working at once."""

    def post(self, request: Request, id: str) -> Response:
        issued = RotateWebhookOperation(request.auth, id).run()
        rotated = _issued(issued)
        return Response(rotated.model_dump())


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
        # `custom` is the app's own type (a signal a sender pushes to): a
        # hosted type by that name would be created as a custom signal, so
        # it is never offered.
        offered = [kind for kind in catalog.types if kind.type != CUSTOM_SIGNAL_TYPE]
        if len(offered) != len(catalog.types):
            logger.warning("signals: the catalog names a hosted type %r; not offered", CUSTOM_SIGNAL_TYPE)
        catalog.types = offered
        return Response(catalog.model_dump())


class SignalTypeEventsView(_SignalsScopedView):
    def get(self, request: Request, signal_type: str) -> Response:
        require_hosted_type(signal_type)
        after = request.query_params.get("after") or None
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        page = self.signals.signal_events(signal=signal_type, after=after, limit=limit)
        return Response(page.model_dump())


class SignalPreviewsView(_SignalsScopedView):
    def post(self, request: Request) -> Response:
        body = PreviewCreateRequest(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        require_hosted_type(data["type"])
        setup = setup_payload(
            filter=data["filter"], prompt=data.get("prompt"), min_confidence=data.get("min_confidence")
        )
        payload = preview_payload(signal_type=data["type"], event_ids=data["event_ids"], setup=setup)
        preview = self.signals.create_preview(payload=payload)
        return Response(preview.model_dump(), status=202)


class SignalPreviewView(_SignalsScopedView):
    def get(self, request: Request, id: str) -> Response:
        preview = self.signals.preview(preview_id=id)
        return Response(preview.model_dump())

    def delete(self, request: Request, id: str) -> Response:
        preview = self.signals.cancel_preview(preview_id=id)
        return Response(preview.model_dump())

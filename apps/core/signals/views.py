"""`/v1/logs` (an account's logs: a sheet fed by one listener) and
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
from sheets.constants import DEFAULT_INDEX_PAGE, MAX_INDEX_PAGE
from sheets.serializers import sheet_summary

from .constants import KEY_LIMIT_DETAIL, SignalsErrorCode
from .operations.logs import (
    CreateLogOperation,
    DeleteLogOperation,
    EditLogOperation,
    ListenerGone,
    Log,
    ReadLogOperation,
    ReconnectLogsOperation,
    UnknownSignal,
)
from .serializers import LogCreateRequest, LogPatchRequest, PreviewCreateRequest
from .services.logs import LocalLog, LogNotFound, LogService
from .services.signals_client import (
    SignalsAccessDenied,
    SignalsClientRequestError,
    SignalsClientService,
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


def _summary(local_log: LocalLog) -> LogSummary:
    sheet = sheet_summary(local_log.sheet)
    feed = local_log.feed
    return LogSummary(
        sheet=sheet,
        listener_id=feed.listener_id,
        signal=feed.signal,
        status=local_log.status,
        last_polled_at=_iso(feed.last_polled_at),
        autofill=feed.autofill,
    )


def _detail(log: Log) -> LogDetail:
    sheet = sheet_summary(log.local_log.sheet)
    feed = log.local_log.feed
    return LogDetail(
        sheet=sheet,
        listener=log.listener,
        signal=feed.signal,
        status=log.local_log.status,
        last_polled_at=_iso(feed.last_polled_at),
        autofill=feed.autofill,
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


class LogsView(_SignalsView):
    def get(self, request: Request) -> Response:
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        logs = self.logs.page(after_id=request.query_params.get("after", ""), limit=limit)
        items = [_summary(log) for log in logs]
        next_cursor = next_cursor_from(logs, limit=limit, cursor=lambda log: log.sheet_id)
        page = LogsPage(items=items, next_cursor=next_cursor)
        return Response(page.model_dump())

    def post(self, request: Request) -> Response:
        body = LogCreateRequest(data=request.data)
        body.is_valid(raise_exception=True)
        log = CreateLogOperation(request.auth, **body.validated_data).run()
        detail = _detail(log)
        return Response(detail.model_dump(), status=201)


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


class SignalsCatalogView(_SignalsView):
    def get(self, request: Request) -> Response:
        catalog = self.signals.catalog()
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

"""The wire layer: every signals-service HTTP call, with transport and
protocol failures normalized into the package's named errors and bodies
validated into the SHARED contract models (openbower_schema) before
anyone downstream touches them. The same shape as
discover.services.index_client.transport: addresses from the package's
urls, the credential from the shared session flow
(auth_client.services.sessions.SessionTokenAuth).
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.sessions import SessionTokenAuth
from openbower_schema import (
    ListenersPage,
    ListenerVersionsPage,
    ListenerWire,
    MatchesPage,
    PreviewRunningWire,
    PreviewRunWire,
    SignalEventsPage,
    SignalsCatalog,
)
from signals.constants import SignalsConflictCode

from . import urls
from .errors import (
    SignalsAccessDenied,
    SignalsClientRequestError,
    SignalsLimitReached,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
)


def _httpx_transport() -> httpx.BaseTransport | None:
    """The client's httpx transport; None means the real network. The one
    seam tests patch (with httpx.MockTransport), so the WHOLE stack, the
    auth flow and its refresh-retry included, runs for real under test."""
    return None


def client_for(session) -> httpx.Client:
    """A signals client acting as the session's user. The auth flow owns
    token attachment and the single refresh-retry; everything else here is
    plain HTTP."""
    kwargs: dict[str, Any] = {
        "auth": SessionTokenAuth(session),
        "timeout": settings.SIGNALS_HTTP_TIMEOUT_SECONDS,
    }
    override = _httpx_transport()
    if override is not None:
        kwargs["transport"] = override
    return httpx.Client(**kwargs)


def _validated[T: BaseModel](body: Any, model: type[T]) -> T:
    """Validate an upstream body into its contract model; a mismatch is a
    bounded upstream error (the service broke the shared shape), never a
    raw 500."""
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise SignalsUpstreamError(f"{model.__name__} body invalid: {e.error_count()} error(s)") from e


def _conflict(body: dict[str, Any]) -> SignalsClientRequestError:
    """A 409 as its own error type, by the service's code."""
    code = body.get("error")
    if code == SignalsConflictCode.LIMIT_REACHED:
        return SignalsLimitReached(409, body)
    if code == SignalsConflictCode.ALREADY_RUNNING:
        running_body = body.get("running")
        running = _validated(running_body, PreviewRunningWire)
        return SignalsPreviewRunning(409, body, running=running)
    return SignalsClientRequestError(409, body)


def _outcome(response: httpx.Response, *, ok: int) -> dict[str, Any] | None:
    """Map a response to a parsed body (None for an expected 204) or a named
    error. 401 here means the auth flow's refresh-retry already happened
    (or the session is dead): callers resolve it with a fresh login."""
    status = response.status_code
    if status >= 500 or status in (408, 429):
        raise SignalsUpstreamUnavailable(f"signals service returned {status}")
    if status == 401:
        raise DownstreamTokenRejected()
    if status == 403:
        # Valid token, missing scope: a refresh will not add it -> re-login.
        raise SignalsAccessDenied()
    if ok == 204 and status == 204:
        return None
    try:
        body = response.json()
    except ValueError as e:
        raise SignalsUpstreamError("signals service returned a non-JSON body") from e
    if not isinstance(body, dict):
        raise SignalsUpstreamError("signals service returned a non-object body")
    # The caller's request being wrong, not an upstream fault: the status
    # and the service's own {error, detail} body pass straight through.
    if status in (400, 404):
        raise SignalsClientRequestError(status, body)
    if status == 409:
        raise _conflict(body)
    if status != ok:
        raise SignalsUpstreamError(f"signals service returned {status}, expected {ok}")
    return body


def _request(
    client: httpx.Client, method: str, url: str, *, ok: int, payload: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    try:
        response = client.request(method, url, json=payload)
    except httpx.HTTPError as e:
        raise SignalsUpstreamUnavailable(f"signals service unreachable: {type(e).__name__}") from e
    return _outcome(response, ok=ok)


def catalog(client: httpx.Client) -> SignalsCatalog:
    body = _request(client, "GET", urls.catalog_url(), ok=200)
    return _validated(body, SignalsCatalog)


def signal_events(client: httpx.Client, *, signal: str, after: str | None, limit: int | None) -> SignalEventsPage:
    url = urls.signal_events_url(signal, after=after, limit=limit)
    body = _request(client, "GET", url, ok=200)
    return _validated(body, SignalEventsPage)


def listeners(client: httpx.Client, *, after: str | None, limit: int | None) -> ListenersPage:
    url = urls.listeners_url(after=after, limit=limit)
    body = _request(client, "GET", url, ok=200)
    return _validated(body, ListenersPage)


def create_listener(client: httpx.Client, *, payload: dict[str, Any]) -> ListenerWire:
    """`payload` is the listener as the service takes it: label, signal,
    filter, and the prompt and min_confidence if any."""
    body = _request(client, "POST", urls.listeners_url(), ok=201, payload=payload)
    return _validated(body, ListenerWire)


def listener(client: httpx.Client, *, listener_id: str) -> ListenerWire:
    body = _request(client, "GET", urls.listener_url(listener_id), ok=200)
    return _validated(body, ListenerWire)


def edit_listener(client: httpx.Client, *, listener_id: str, payload: dict[str, Any]) -> ListenerWire:
    """`payload` is a new label, a whole new setup (filter, with its prompt
    and min_confidence if any), or both."""
    body = _request(client, "PATCH", urls.listener_url(listener_id), ok=200, payload=payload)
    return _validated(body, ListenerWire)


def delete_listener(client: httpx.Client, *, listener_id: str) -> None:
    _request(client, "DELETE", urls.listener_url(listener_id), ok=204)


def listener_versions(
    client: httpx.Client, *, listener_id: str, after: str | None, limit: int | None
) -> ListenerVersionsPage:
    url = urls.listener_versions_url(listener_id, after=after, limit=limit)
    body = _request(client, "GET", url, ok=200)
    return _validated(body, ListenerVersionsPage)


def listener_matches(client: httpx.Client, *, listener_id: str, after: str | None, limit: int | None) -> MatchesPage:
    url = urls.listener_matches_url(listener_id, after=after, limit=limit)
    body = _request(client, "GET", url, ok=200)
    return _validated(body, MatchesPage)


def create_preview(client: httpx.Client, *, payload: dict[str, Any]) -> PreviewRunWire:
    """`payload` is a signal, a setup as a listener's would be given, and the
    ids of the signal's events to try it on. 202 either way: complete at
    once when the filter decided every event, else poll it."""
    body = _request(client, "POST", urls.previews_url(), ok=202, payload=payload)
    return _validated(body, PreviewRunWire)


def preview(client: httpx.Client, *, preview_id: str) -> PreviewRunWire:
    body = _request(client, "GET", urls.preview_url(preview_id), ok=200)
    return _validated(body, PreviewRunWire)


def cancel_preview(client: httpx.Client, *, preview_id: str) -> PreviewRunWire:
    """Stop an open preview; a finished one comes back as it was."""
    body = _request(client, "DELETE", urls.preview_url(preview_id), ok=200)
    return _validated(body, PreviewRunWire)

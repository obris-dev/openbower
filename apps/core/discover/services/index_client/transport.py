"""The wire layer: every upstream data-service HTTP call, with transport
and protocol failures normalized into the package's named errors and
bodies validated into the SHARED contract models (openbower_schema)
before anyone downstream touches them, so this consumer cannot drift from
what the data service serializes. Addresses come from the package's urls;
credentials come from the client's auth flow (auth.py); this module owns
only the calls and their outcome mapping.
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

from auth_client.downstream import DownstreamTokenRejected
from openbower_schema import LookalikeListResponse

from . import urls
from .auth import SessionTokenAuth
from .errors import IndexAccessDenied, IndexClientRequestError, IndexUpstreamError, IndexUpstreamUnavailable


def _httpx_transport() -> httpx.BaseTransport | None:
    """The client's httpx transport; None means the real network. The one
    seam tests patch (with httpx.MockTransport), so the WHOLE stack, the
    auth flow and its refresh-retry included, runs for real under test."""
    return None


def client_for(session) -> httpx.Client:
    """A data-service client acting as the session's user. The auth flow
    owns token attachment and the single refresh-retry; everything else
    here is plain HTTP."""
    kwargs: dict[str, Any] = {
        "auth": SessionTokenAuth(session),
        "timeout": settings.DATA_HTTP_TIMEOUT_SECONDS,
    }
    override = _httpx_transport()
    if override is not None:
        kwargs["transport"] = override
    return httpx.Client(**kwargs)


def _outcome(response: httpx.Response, *, ok: tuple[int, ...]) -> dict[str, Any]:
    """Map a response to a parsed body or a named error. 401 here means
    the auth flow's refresh-retry already happened (or the session is
    dead): the token genuinely cannot reach data, which callers resolve
    with a fresh login."""
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise IndexUpstreamUnavailable(f"data service returned {response.status_code}")
    if response.status_code == 401:
        raise DownstreamTokenRejected()
    if response.status_code == 403:
        # Valid token, missing scope: a refresh will not add it -> re-login.
        raise IndexAccessDenied()
    try:
        body = response.json()
    except ValueError as e:
        raise IndexUpstreamError("data service returned a non-JSON body") from e
    # 400 (bad query) and 404 (unknown run) are the caller's request being
    # wrong, not an upstream fault: pass the status + the data service's
    # own {error, detail} body straight through.
    if response.status_code in (400, 404):
        raise IndexClientRequestError(response.status_code, body)
    # 202 is the async lifecycle's "run accepted, poll it": a success body
    # (status pending/running) on the same validated envelope.
    if response.status_code not in ok:
        raise IndexUpstreamError(f"data service returned {response.status_code}")
    if not isinstance(body, dict):
        raise IndexUpstreamError("data service returned a non-object body")
    return body


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    ok: tuple[int, ...] = (200, 202),
) -> dict[str, Any]:
    try:
        response = client.get(url) if method == "GET" else client.post(url, json=payload)
    except httpx.HTTPError as e:
        raise IndexUpstreamUnavailable(f"data service unreachable: {type(e).__name__}") from e
    return _outcome(response, ok=ok)


def _validated[T: BaseModel](body: dict[str, Any], model: type[T]) -> T:
    """Validate an upstream body into its contract model; a mismatch is a
    bounded upstream error (the data service broke the shared shape),
    never a raw 500."""
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise IndexUpstreamError(f"{model.__name__} body invalid: {e.error_count()} error(s)") from e


def lookalikes(client: httpx.Client, *, payload: dict[str, Any]) -> LookalikeListResponse:
    """Run a look-alike query. `payload` carries the already-shaped query
    (domains | company_ids, limit, cursor). The envelope's `status` says
    whether `items` is a real page (complete) or the run is still
    computing (pending)."""
    return _validated(_request(client, "POST", urls.lookalikes_url(), payload=payload), LookalikeListResponse)


def run_status(client: httpx.Client, *, run_id: str, limit: int | None = None) -> LookalikeListResponse:
    """Poll an async run: the same envelope, page included once complete.
    `limit` sizes the completed page; forwarded so a warm cohort (answered
    inline) and a cold one (answered via this poll) return the same page."""
    return _validated(_request(client, "GET", urls.lookalike_run_url(run_id, limit=limit)), LookalikeListResponse)


def cancel_run(client: httpx.Client, *, run_id: str) -> LookalikeListResponse:
    """Stop a pending/running run. Same envelope as the poll: the response
    reports the post-cancel status (a terminal run stays whatever it
    already was)."""
    return _validated(_request(client, "POST", urls.lookalike_run_cancel_url(run_id)), LookalikeListResponse)

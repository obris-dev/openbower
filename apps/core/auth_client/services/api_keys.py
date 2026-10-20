"""Machine API keys, proxied to the identity service AS the signed-in
user: the browser never holds the user's upstream token, so the app
dials the service with the session's token (the same auth flow the data
client uses, refresh-retry included) and relays what it says. Core
stores nothing about keys; a key is verified on the machine lane by
introspection (resource_server), never by anything kept here."""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from pydantic import ValidationError

from auth_client import idp_urls
from openbower_schema.auth import ApiKeyMinted, ApiKeysList

from ..downstream import DownstreamTokenRejected
from .oauth.errors import AuthUpstreamError, AuthUpstreamUnavailable
from .sessions import SessionTokenAuth


class ApiKeyRefused(Exception):
    """The identity service refused in its own words, relayed verbatim:
    `status` with the {error, detail} body (a bad name, keys disabled on
    the deployment, the per-user limit, an unknown key)."""

    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.error = error
        self.detail = detail


def _httpx_transport() -> httpx.BaseTransport | None:
    """The client's httpx transport; None means the real network. The one
    seam tests patch (with httpx.MockTransport), so the whole stack, the
    auth flow and its refresh-retry included, runs for real under test."""
    return None


def _client(session) -> httpx.Client:
    kwargs: dict[str, Any] = {
        "auth": SessionTokenAuth(session),
        "timeout": settings.AUTH_HTTP_TIMEOUT_SECONDS,
    }
    override = _httpx_transport()
    if override is not None:
        kwargs["transport"] = override
    return httpx.Client(**kwargs)


# The refusals the web can act on: a bad request or a lost race renders
# its detail, an unknown key is handled by the caller. Any other status
# the identity service might answer is not relayed, since the web reads
# 401 and 403 as the login boundary and every other body as internals.
_RELAYED_STATUSES = (400, 404, 409)


def _call(session, method: str, url: str, *, json: dict[str, Any] | None = None) -> Any:
    """One request as the user, settled into a body or a named error.
    A 401 standing after the auth flow's one refresh is the session's
    death; 5xx and the gateway's 408/429 are transient; a 400, 404 or
    409 with the envelope shape is the service's refusal, relayed; any
    other status is an upstream error."""
    try:
        with _client(session) as client:
            response = client.request(method, url, json=json)
    except httpx.HTTPError as e:
        raise AuthUpstreamUnavailable(f"api keys endpoint unreachable: {type(e).__name__}") from e
    if response.status_code == 401:
        raise DownstreamTokenRejected()
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise AuthUpstreamUnavailable(f"api keys endpoint returned {response.status_code}")
    if response.status_code == 204:
        return None
    try:
        body = response.json()
    except ValueError as e:
        raise AuthUpstreamError("api keys response was not JSON") from e
    if response.status_code >= 400:
        enveloped = isinstance(body, dict) and "error" in body and "detail" in body
        if response.status_code in _RELAYED_STATUSES and enveloped:
            raise ApiKeyRefused(response.status_code, str(body["error"]), str(body["detail"]))
        raise AuthUpstreamError(f"api keys endpoint returned {response.status_code}")
    return body


class ApiKeyService:
    """The signed-in user's keys, through their session."""

    def __init__(self, session) -> None:
        self.session = session

    def list(self) -> ApiKeysList:
        body = _call(self.session, "GET", idp_urls.api_keys_url())
        try:
            return ApiKeysList.model_validate(body)
        except ValidationError as e:
            raise AuthUpstreamError(f"api keys list invalid: {e.error_count()} error(s)") from e

    def mint(self, name: str) -> ApiKeyMinted:
        body = _call(self.session, "POST", idp_urls.api_keys_url(), json={"name": name})
        try:
            return ApiKeyMinted.model_validate(body)
        except ValidationError as e:
            raise AuthUpstreamError(f"api key mint response invalid: {e.error_count()} error(s)") from e

    def revoke(self, key_id: str) -> None:
        """Idempotent: a key the service no longer knows (revoked from
        another tab, or never the user's) is the outcome asked for, so
        its 404 is a success here, never a refusal the row would keep
        showing."""
        try:
            _call(self.session, "DELETE", idp_urls.api_key_url(key_id))
        except ApiKeyRefused as e:
            if e.status != 404:
                raise

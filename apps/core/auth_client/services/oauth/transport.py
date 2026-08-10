"""The wire layer: every upstream HTTP call, with transport and protocol
failures normalized into the package's named errors and bodies validated
before anyone downstream touches them. Addresses come from
`auth_client.idp_urls` (the IdP endpoint catalog); this module owns only
the calls."""

from __future__ import annotations

import httpx
from django.conf import settings
from pydantic import ValidationError

from auth_client import idp_urls
from openbower_schema import AuthUser

from .errors import AuthUpstreamError, AuthUpstreamUnavailable
from .schema import TokenResponse


def post_token(data: dict[str, str | list[str]]) -> TokenResponse:
    """POST the token endpoint; parse the body into a validated
    TokenResponse."""
    try:
        response = httpx.post(
            idp_urls.token_url(),
            data=data,
            timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        # Transport failure (connect/timeout/read): transient, not a rejection.
        raise AuthUpstreamUnavailable(f"token endpoint unreachable: {type(e).__name__}") from e
    # Transient statuses: 5xx (server blip) and 408/429 (timeout / rate-limited,
    # often injected by a fronting gateway/WAF, not the IdP). Treating these as
    # terminal would revoke a live session over a passing hiccup, so the token
    # may well still be valid; retry later.
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise AuthUpstreamUnavailable(f"token endpoint returned {response.status_code}")
    if response.status_code != 200:
        # Other 4xx: a definitive rejection the retry won't fix (invalid_grant,
        # invalid_client, unauthorized_client).
        raise AuthUpstreamError(f"token endpoint returned {response.status_code}")
    try:
        body = response.json()
    except ValueError as e:
        raise AuthUpstreamError("token response was not JSON") from e
    # A malformed body becomes a bounded upstream error, not a 500 downstream.
    try:
        return TokenResponse.model_validate(body)
    except ValidationError as e:
        raise AuthUpstreamError(f"token response invalid: {e.error_count()} error(s)") from e


def fetch_identity(access_token: str) -> AuthUser:
    """GET the IdP's userinfo, validated against the SHARED AuthUser
    contract (the same model the web validates /me with), so the app
    cannot drift from what the IdP emits."""
    try:
        response = httpx.get(
            idp_urls.me_url(),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        # Transient, mirroring post_token: a blip fetching identity must
        # not be reported as a terminal rejection (errors.py's split is
        # load-bearing for what callers revoke).
        raise AuthUpstreamUnavailable(f"identity endpoint unreachable: {type(e).__name__}") from e
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise AuthUpstreamUnavailable(f"identity endpoint returned {response.status_code}")
    if response.status_code != 200:
        raise AuthUpstreamError(f"identity endpoint returned {response.status_code}")
    try:
        identity = response.json()
    except ValueError as e:
        raise AuthUpstreamError("identity response was not JSON") from e
    try:
        return AuthUser.model_validate(identity)
    except ValidationError as e:
        raise AuthUpstreamError(f"identity response invalid: {e.error_count()} error(s)") from e


def post_revoke(data: dict[str, str]) -> None:
    """Best-effort revocation; transport failures are the CALLER'S choice
    to swallow (the flow layer documents why)."""
    httpx.post(
        idp_urls.revoke_token_url(),
        data=data,
        timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
    )

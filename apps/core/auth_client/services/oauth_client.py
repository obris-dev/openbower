"""The OAuth 2.0 client half of the login handshake.

The app is a PUBLIC client of openbower-auth: Authorization Code + PKCE,
no client secret (possession of the PKCE verifier is the proof). The
short-lived state -> verifier bag lives in OAuthStateStore during the
browser round-trip (see state_store.py); everything durable lands on the
AppSession record (see sessions.py).

All upstream calls (token exchange, /me, refresh, revoke) are
server-to-IdP over httpx with a hard timeout; the browser never holds an
IdP token.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

from auth_client import idp_urls
from openbower_schema import AuthUser

from .state_store import OAuthStateStore


class TokenResponse(BaseModel):
    """The IdP token-endpoint shape the app consumes (code exchange +
    refresh). App-local: it's this client's parse of the standard OAuth
    response, not a domain contract we own. Pydantic does the validation
    (required fields, expires_in numeric) declaratively."""

    access_token: str
    refresh_token: str
    expires_in: int


class OAuthClientError(Exception):
    """Base for login-handshake failures."""


class StateMismatch(OAuthClientError):
    """The callback's state is unknown or expired (possible CSRF, an
    expired login attempt, or a replayed callback)."""


class AuthUpstreamError(OAuthClientError):
    """The IdP DEFINITIVELY rejected a server-side call: a 4xx the retry
    won't fix (e.g. 400 invalid_grant on a dead refresh token). Callers
    treat this as terminal, the session is over.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuthUpstreamUnavailable(AuthUpstreamError):
    """The IdP was unreachable or failed TRANSIENTLY (network error,
    timeout, 5xx). Distinct from AuthUpstreamError so the refresh path can
    tell "the token is dead" (revoke the session) apart from "the IdP
    blinked" (keep the session, a brief outage must not log everyone out).
    Subclasses AuthUpstreamError so callers that don't care about the
    distinction (the login callback) still catch both.
    """


def _redirect_uri() -> str:
    return f"{settings.BASE_URL}/{settings.API_VERSION_PREFIX}/auth/callback"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _post_token(data: dict[str, str]) -> TokenResponse:
    """POST the IdP token endpoint; normalize transport + protocol errors and
    parse the body into a validated TokenResponse."""
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
    # Pydantic validates required fields + coerces expires_in to int; a
    # malformed body becomes a bounded upstream error, not a 500 downstream.
    try:
        return TokenResponse.model_validate(body)
    except ValidationError as e:
        raise AuthUpstreamError(f"token response invalid: {e.error_count()} error(s)") from e


class OAuthClientGlobal:
    """System-level helpers for the Authorization Code + PKCE flow (no
    account context; the browser session is user-scoped)."""

    @staticmethod
    def begin_login() -> str:
        """Mint state + PKCE verifier, stash the bag, and return the
        IdP authorize URL to redirect the browser to."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        OAuthStateStore.put(state=state, verifier=verifier)
        return idp_urls.authorize_url(
            {
                "response_type": "code",
                "client_id": settings.OAUTH_CLIENT_ID,
                "redirect_uri": _redirect_uri(),
                # data:read/data:write let the backend read (look-alikes) and
                # manage saved seed sets on the user's behalf; the IdP does not
                # grant either by default. data:write is requested NOW even
                # though the UI only reads today: a DELIBERATE forward-compat
                # choice so existing sessions need no re-login (scope upgrade)
                # when the seed-set write UI ships. The token never reaches the
                # browser (encrypted at rest), so the extra grant's blast
                # radius is bounded; revisit if a broader/third-party scope
                # appears.
                "scope": "profile data:read data:write",
                # RFC 8707 resource indicators (repeated param): bind the
                # token to exactly the resource servers it is used at (the IdP
                # userinfo + the data service), so it can't be replayed
                # elsewhere. Encoded with doseq in authorize_url.
                "resource": settings.OAUTH_RESOURCES,
                "state": state,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            }
        )

    @staticmethod
    def complete_login(*, state: str, code: str) -> tuple[TokenResponse, AuthUser]:
        """Exchange the callback's code for tokens and resolve identity.

        Returns `(tokens, user)`: a validated TokenResponse and the IdP's
        identity parsed into the shared AuthUser contract. Raises
        StateMismatch on an unknown/expired state, AuthUpstreamError on any
        IdP failure. The state bag is consumed by the pop BEFORE the exchange
        so a replayed callback can't race a second exchange with the same
        verifier.
        """
        verifier = OAuthStateStore.pop(state=state)
        if verifier is None:
            raise StateMismatch(state)

        tokens = _post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(),
                "client_id": settings.OAUTH_CLIENT_ID,
                "code_verifier": verifier,
            }
        )

        try:
            me = httpx.get(
                idp_urls.me_url(),
                headers={"Authorization": f"Bearer {tokens.access_token}"},
                timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise AuthUpstreamError(f"identity endpoint unreachable: {type(e).__name__}") from e
        if me.status_code != 200:
            raise AuthUpstreamError(f"identity endpoint returned {me.status_code}")
        try:
            identity = me.json()
        except ValueError as e:
            raise AuthUpstreamError("identity response was not JSON") from e
        # Validate against the SHARED AuthUser contract (the same model the
        # web validates /me with): the app can't drift from what the IdP emits.
        try:
            user = AuthUser.model_validate(identity)
        except ValidationError as e:
            raise AuthUpstreamError(f"identity response invalid: {e.error_count()} error(s)") from e
        return tokens, user

    @staticmethod
    def refresh(*, refresh_token: str) -> TokenResponse:
        """Rotate the token pair. Raises AuthUpstreamError when the IdP
        rejects it (revoked / expired refresh token), which callers treat
        as "session over"."""
        return _post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.OAUTH_CLIENT_ID,
            }
        )

    @staticmethod
    def revoke(*, token: str, token_type_hint: str = "access_token") -> None:
        """Best-effort upstream revocation on logout. Failures are
        swallowed: the local session is already dead, and the tokens
        time out upstream anyway. Pass the refresh token (with its
        hint) to kill the whole pair; the IdP cascades refresh -> access.
        """
        with contextlib.suppress(httpx.HTTPError):
            httpx.post(
                idp_urls.revoke_token_url(),
                data={
                    "token": token,
                    "token_type_hint": token_type_hint,
                    "client_id": settings.OAUTH_CLIENT_ID,
                },
                timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
            )


class OAuthClientService:
    """OAuth 2.0 client for openbower-auth. All operations are system-level
    (the browser session is user-scoped, not account-scoped), so they live
    under `Global`, mirroring the accounts services' shape."""

    Global = OAuthClientGlobal

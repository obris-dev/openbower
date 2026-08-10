"""The OAuth 2.0 client half of the login handshake, top to bottom as the
flow runs: begin (authorize URL out), complete (code in, tokens +
identity out), refresh, revoke.

The app is a PUBLIC client of the identity service: Authorization Code +
PKCE, no client secret. The short-lived state -> verifier bag lives in
OAuthStateStore during the browser round-trip (see state_store.py);
everything durable lands on the AppSession record (see sessions.py). All
upstream calls go through transport.py; the browser never holds an IdP
token.
"""

from __future__ import annotations

import contextlib

import httpx
from django.conf import settings

from auth_client import idp_urls
from openbower_schema import AuthUser

from ..state_store import OAuthStateStore
from . import pkce, transport
from .errors import StateMismatch
from .schema import TokenResponse


def _redirect_uri() -> str:
    """OUR callback (an app URL, not an IdP one): sent at authorize AND
    token exchange, which the IdP requires to match."""
    return f"{settings.BASE_URL}/{settings.API_VERSION_PREFIX}/auth/callback"


class OAuthClientGlobal:
    """System-level operations for the Authorization Code + PKCE flow (no
    account context; the browser session is user-scoped)."""

    @staticmethod
    def begin_login() -> tuple[str, str]:
        """Mint state + PKCE verifier, stash the bag, and return
        `(authorize_url, state)`. The state goes back to the caller so
        the view can ALSO pin it to the browser in a cookie: the cache
        bag alone is server-global and cannot say which browser started
        the flow."""
        state = pkce.new_state()
        verifier = pkce.new_verifier()
        OAuthStateStore.put(state=state, verifier=verifier)
        url = idp_urls.authorize_url(
            {
                "response_type": "code",
                "client_id": settings.OAUTH_CLIENT_ID,
                "redirect_uri": _redirect_uri(),
                # Scopes grow with the product's phases; sessions minted
                # before a scope existed need a re-login to gain it.
                "scope": "profile",
                # RFC 8707 resource indicators (repeated param): bind the
                # token to exactly the resource servers it is used at, so
                # it cannot be replayed elsewhere. Encoded with doseq in
                # authorize_url.
                "resource": settings.OAUTH_RESOURCES,
                "state": state,
                "code_challenge": pkce.challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return url, state

    @staticmethod
    def complete_login(*, state: str, code: str) -> tuple[TokenResponse, AuthUser]:
        """Exchange the callback's code for tokens and resolve identity.

        Raises StateMismatch on an unknown/expired state, AuthUpstreamError
        on any IdP failure. The state bag is consumed by the pop BEFORE the
        exchange so a replayed callback can't race a second exchange with
        the same verifier.
        """
        verifier = OAuthStateStore.pop(state=state)
        if verifier is None:
            raise StateMismatch(state)
        tokens = transport.post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(),
                "client_id": settings.OAUTH_CLIENT_ID,
                "code_verifier": verifier,
                # RFC 8707 on the TOKEN request too: audience is minted at
                # token time, so an authorize-only indicator would be lost
                # by an audience-enforcing resource server.
                "resource": settings.OAUTH_RESOURCES,
            }
        )
        return tokens, transport.fetch_identity(tokens.access_token)

    @staticmethod
    def refresh(*, refresh_token: str) -> TokenResponse:
        """Rotate the token pair. Raises AuthUpstreamError when the IdP
        rejects it (revoked / expired refresh token), which callers treat
        as "session over"."""
        return transport.post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.OAUTH_CLIENT_ID,
                # Without the indicator here, the FIRST silent refresh
                # (about an hour in) would mint an audience-less token and
                # every audience-checking call after it would 401.
                "resource": settings.OAUTH_RESOURCES,
            }
        )

    @staticmethod
    def revoke(*, token: str, token_type_hint: str = "access_token") -> None:
        """Best-effort upstream revocation on logout. Failures are
        swallowed: the local session is already dead, and the tokens time
        out upstream anyway. Pass the refresh token (with its hint) to
        kill the whole pair; the IdP cascades refresh -> access."""
        with contextlib.suppress(httpx.HTTPError):
            transport.post_revoke(
                {
                    "token": token,
                    "token_type_hint": token_type_hint,
                    "client_id": settings.OAUTH_CLIENT_ID,
                }
            )


class OAuthClientService:
    """OAuth 2.0 client of the identity service. All operations are
    system-level (the browser session is user-scoped, not account-scoped),
    so they live under `Global`, mirroring the accounts services' shape."""

    Global = OAuthClientGlobal

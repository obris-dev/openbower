"""Set/clear the `bwr_session` cookie in one place so the attributes can't
drift between login and logout paths.

HttpOnly (no script access), SameSite=Lax (attached on top-level
navigations, e.g. returning from the IdP redirect, but NOT on cross-site
POSTs, which is the CSRF posture), Secure per env (off for plain-HTTP dev).
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

from .constants import SESSION_COOKIE_NAME, STATE_COOKIE_NAME, STATE_TTL_SECONDS


def set_session_cookie(response: HttpResponse, value: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=settings.AUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="Lax",
        # Empty = host-only (dev default). A deploy that wants sibling
        # origins to SEE the session (the marketing navbar's logged-in
        # state) scopes it to the parent domain, e.g. ".openbower.ai".
        domain=settings.AUTH_COOKIE_DOMAIN or None,
    )


def set_state_cookie(response: HttpResponse, state: str) -> None:
    """Pin the login round-trip to this browser (see STATE_COOKIE_NAME).

    SameSite=Lax still ships it on the top-level navigation back from the
    IdP, which is exactly the one request that must carry it. Host-only
    on purpose: no sibling origin has business seeing a handshake nonce.
    """
    response.set_cookie(
        STATE_COOKIE_NAME,
        state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="Lax",
    )


def delete_state_cookie(response: HttpResponse) -> None:
    response.delete_cookie(STATE_COOKIE_NAME, samesite="Lax")


def delete_session_cookie(response: HttpResponse) -> None:
    # Match samesite so the deletion reliably overwrites the cookie (path is
    # "/" on both set and delete via Django's default). delete_cookie has no
    # `secure` kwarg; it derives Secure from the cookie name prefix, which
    # bwr_session does not use, so none is needed.
    response.delete_cookie(SESSION_COOKIE_NAME, samesite="Lax", domain=settings.AUTH_COOKIE_DOMAIN or None)

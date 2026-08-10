"""DRF authentication over the `bwr_session` cookie.

Resolves the opaque cookie token to the server-side AppSession (rotating
the IdP token pair when expired; see sessions.py). A present-but-dead
cookie raises AuthenticationFailed so the client knows to re-login;
an absent cookie falls through unauthenticated.

CSRF posture: the cookie is SameSite=Lax, so browsers don't attach it to
cross-site POSTs at all, which covers the API's unsafe methods (logout)
without a token dance. Revisit if the cookie ever needs SameSite=None.
"""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .constants import SESSION_COOKIE_NAME
from .models import AppSession
from .services import AppSessionService


class AppUser:
    """The request principal: a thin projection of the IdP identity carried
    by the session record. NOT a Django model user; the app holds no
    account authority (the cloud mints identities, the app borrows them).
    """

    is_authenticated = True

    def __init__(self, session: AppSession) -> None:
        self.id = session.user_id
        self.email = session.email
        self.account_id = session.account_id
        self.session = session

    def __str__(self) -> str:
        return self.email


class AppSessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        raw = request.COOKIES.get(SESSION_COOKIE_NAME)
        if not raw:
            return None
        session = AppSessionService.Global.resolve(raw)
        if session is None:
            raise AuthenticationFailed("session expired or revoked")
        return (AppUser(session), session)

    def authenticate_header(self, request):
        # Returning a challenge makes DRF answer an unauthenticated/failed
        # request with 401, not 403: without this, DRF downgrades to 403 and
        # a client can't tell "log in again" (expired session) from
        # "forbidden". The value is the WWW-Authenticate header; this API
        # authenticates by cookie, so name that rather than a bearer scheme.
        return "Cookie"

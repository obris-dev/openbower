"""DRF authentication for the app's two credentials: the
`bwr_session` cookie (browsers) and `Bearer obw_...` personal access
tokens (machines). One Authorization header serves every machine
credential, dispatched by prefix; each class claims only its own
shape and falls through otherwise, so the two compose in a per-view
authentication_classes list.

Cookie: resolves the opaque token to the server-side AppSession
(rotating the IdP token pair when expired; see sessions.py). A
present-but-dead cookie raises AuthenticationFailed so the client
knows to re-login; an absent cookie falls through unauthenticated.
CSRF posture: SameSite=Lax covers the API's unsafe methods without a
token dance; revisit if the cookie ever needs SameSite=None.

PAT: a bearer carrying the PAT prefix is ALWAYS judged as a PAT; an
unknown, revoked, or expired one raises rather than falling through,
so a dead key can never silently downgrade to some other credential.
PatAuthentication is attached per-view on machine-facing endpoints
only, never globally: a session-less principal must never reach a
view that reads request.user.session (discover does).
"""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .constants import SESSION_COOKIE_NAME
from .models import AppSession
from .services import AppSessionService, pats


class AppUser:
    """The request principal: a thin projection of the IdP identity,
    whichever credential carried it. NOT a Django model user; the app
    holds no account authority (the cloud mints identities, the app
    borrows them). `session` is None for machine credentials, and any
    view that reads it must therefore never accept one.
    """

    is_authenticated = True

    def __init__(self, *, id: str, email: str, account_id: str, session: AppSession | None = None) -> None:
        self.id = id
        self.email = email
        self.account_id = account_id
        self.session = session

    @classmethod
    def from_session(cls, session: AppSession) -> AppUser:
        return cls(id=session.user_id, email=session.email, account_id=session.account_id, session=session)

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
        return (AppUser.from_session(session), session)

    def authenticate_header(self, request):
        # Returning a challenge makes DRF answer an unauthenticated/failed
        # request with 401, not 403: without this, DRF downgrades to 403 and
        # a client can't tell "log in again" (expired session) from
        # "forbidden". The value is the WWW-Authenticate header; this API
        # authenticates by cookie, so name that rather than a bearer scheme.
        return "Cookie"


class PatAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        raw = header.removeprefix("Bearer ").strip()
        if not raw.startswith(pats.TOKEN_PREFIX):
            # Not this credential's shape: fall through so another
            # class (a future OAuth bearer leg) may claim it.
            return None
        record = pats.resolve(raw)
        if record is None:
            raise AuthenticationFailed("invalid or revoked token")
        principal = AppUser(id=record.user_id, email="", account_id=record.account_id)
        # A marker, never the secret: consumers test WHICH credential
        # authenticated (the mint endpoint refuses PATs), and the raw
        # must not ride the request object.
        return (principal, "pat")

    def authenticate_header(self, request):
        return "Bearer"

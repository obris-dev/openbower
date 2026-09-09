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
only, never globally: a PAT sets request.auth to a marker, not a
session, so a view that reads request.auth as a session (discover,
for its index-client token) must never accept one.
"""

from __future__ import annotations

from functools import cached_property

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .constants import SESSION_COOKIE_NAME
from .models import AppSession, PersonalAccessToken
from .services import AppSessionService, pats


class AppUser:
    """The request principal: a uniform projection of the IdP identity,
    the SAME shape whichever credential carried it (id, email,
    account_id, always all three). NOT a Django model user; the app
    holds no account authority (the cloud mints identities, the app
    borrows them). How the request authenticated is NOT here: the
    credential (a session, a PAT) rides request.auth, DRF's slot for
    exactly that, so identity and mechanism never entangle.
    """

    is_authenticated = True

    def __init__(self, *, id: str, account_id: str) -> None:
        self.id = id
        self.account_id = account_id

    @classmethod
    def from_session(cls, session: AppSession) -> AppUser:
        return cls(id=session.user_id, account_id=session.account_id)

    @classmethod
    def from_pat(cls, pat: PersonalAccessToken) -> AppUser:
        return cls(id=pat.user_id, account_id=pat.account_id)

    @cached_property
    def email(self) -> str:
        # Display only, and DERIVED, never stored per-credential: the
        # email is a projection of the IdP's /me whose one local home
        # is the login record, so resolve it from the freshest login
        # for this user (absent when they have logged out everywhere,
        # which only display would ever notice). Lazy: the query runs
        # only when something actually renders the email.
        session = AppSession.objects.filter(user_id=self.id).order_by("-id").first()
        return session.email if session else ""

    def __str__(self) -> str:
        # The stable identifier, never the derived email: __str__ runs
        # in log lines, and neither a DB query nor PII belongs there.
        return self.id


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
        # request.auth = the PAT record: DRF's credential slot, typed,
        # so consumers test the credential by ITS TYPE (the mint
        # endpoint refuses a PAT) rather than a magic string, and the
        # raw secret never rides the request. Mirrors the cookie path,
        # whose request.auth is the AppSession record.
        return (AppUser.from_pat(record), record)

    def authenticate_header(self, request):
        return "Bearer"

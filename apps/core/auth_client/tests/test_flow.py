"""End-to-end login -> me -> logout flow for the app's OAuth-client surface.

The whole app side runs for real (views, PKCE state store, AppSession record,
cookie handling, session auth); only the outbound httpx calls to the IdP
(token exchange, /me, revoke) are mocked. This is the regression guard for
the class of bug that a static review and typecheck can't catch, e.g. a bad
Django kwarg in delete_session_cookie that 500s logout.

Run: DJANGO_ENV=test uv run python manage.py test auth_client
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse

from auth_client.constants import SESSION_COOKIE_NAME
from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.oauth import AuthUpstreamUnavailable, TokenResponse
from auth_client.services.sessions import AppSessionService
from openbower_schema import AuthUser

# A valid /me identity: id / account_id are 26-char ULID-shaped strings so
# they fit the AppSession char-pointer columns.
_IDENTITY = {
    "id": "01JQ" + "A" * 22,
    "email": "josh@example.com",
    "account_id": "01JQ" + "B" * 22,
}
_TOKENS = {"access_token": "access-abc", "refresh_token": "refresh-abc", "expires_in": 3600}


class _Resp:
    """Minimal stand-in for an httpx.Response (status_code + json())."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


class LoginLogoutFlowTests(TestCase):
    def _state_from_login(self) -> str:
        """Drive GET /v1/auth/login and pull the PKCE `state` out of the
        authorize redirect (the real begin_login stashed the verifier)."""
        resp = self.client.get(reverse("auth_login"))
        self.assertEqual(resp.status_code, 302)
        query = parse_qs(urlparse(resp["Location"]).query)
        return query["state"][0]

    def test_login_me_logout_roundtrip(self):
        state = self._state_from_login()

        # Callback: exchange the code + resolve identity, both mocked at the
        # httpx boundary so the real callback / session-create code runs.
        with (
            patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(200, _TOKENS)),
            patch("auth_client.services.oauth.transport.httpx.get", return_value=_Resp(200, _IDENTITY)),
        ):
            cb = self.client.get(reverse("auth_callback"), {"code": "the-code", "state": state})
        self.assertEqual(cb.status_code, 302)
        self.assertTrue(self.client.cookies.get(SESSION_COOKIE_NAME))
        self.assertTrue(self.client.cookies[SESSION_COOKIE_NAME].value)

        # /me resolves the cookie to the identity projection (no IdP call: the
        # access token is fresh, so no refresh).
        who = self.client.get(reverse("auth_me"))
        self.assertEqual(who.status_code, 200)
        self.assertEqual(who.json()["email"], "josh@example.com")

        # Logout must be 200 (the regression: a bad delete_cookie kwarg 500'd
        # here), return the IdP logout URL, and clear the cookie.
        with patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(200, {})):
            out = self.client.post(reverse("auth_logout"))
        self.assertEqual(out.status_code, 200)
        self.assertIn("idp_logout_url", out.json())
        self.assertEqual(self.client.cookies[SESSION_COOKIE_NAME].value, "")

        # After logout the session is gone: /me is unauthenticated (401).
        after = self.client.get(reverse("auth_me"))
        self.assertEqual(after.status_code, 401)

    def test_logout_without_session_is_idempotent(self):
        # A logout with no cookie still 200s and returns the IdP logout URL,
        # never 500s (exercises the delete_cookie path with no session).
        out = self.client.post(reverse("auth_logout"))
        self.assertEqual(out.status_code, 200)
        self.assertIn("idp_logout_url", out.json())

    def test_me_unauthenticated_is_401(self):
        resp = self.client.get(reverse("auth_me"))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "not_authenticated")


_REFRESHED = {"access_token": "access-def", "refresh_token": "refresh-def", "expires_in": 3600}


class SessionRefreshTests(TestCase):
    """The session-owned token lifecycle: early (skew) refresh so a token is
    never handed out about to expire, and the force-refresh / retry primitive
    that every downstream-calling view leans on."""

    def _session(self, *, expires_in: int):
        user = AuthUser(id=_IDENTITY["id"], email=_IDENTITY["email"], account_id=_IDENTITY["account_id"])
        tokens = TokenResponse(access_token="access-abc", refresh_token="refresh-abc", expires_in=expires_in)
        return AppSessionService.Global.create(user=user, tokens=tokens)

    def test_token_within_skew_is_refreshed_early(self):
        # Access token with only a few seconds of life (inside the skew buffer):
        # resolve() must rotate it NOW rather than hand out a token that would
        # lapse in-flight at a downstream service.
        _, raw = self._session(expires_in=5)
        with patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(200, _REFRESHED)) as idp:
            resolved = AppSessionService.Global.resolve(raw)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.access_token, "access-def")
        self.assertEqual(idp.call_count, 1)

    def test_comfortably_fresh_token_is_not_refreshed(self):
        # A token well outside the skew is handed out untouched (no IdP call).
        _, raw = self._session(expires_in=3600)
        with patch("auth_client.services.oauth.transport.httpx.post") as idp:
            resolved = AppSessionService.Global.resolve(raw)
        self.assertEqual(resolved.access_token, "access-abc")
        idp.assert_not_called()

    def test_force_refresh_skips_idp_when_a_peer_already_rotated(self):
        # If the row's current token differs from the one the caller tried, a
        # peer already rotated: return it without spending another refresh.
        record, _ = self._session(expires_in=3600)
        with patch("auth_client.services.oauth.transport.httpx.post") as idp:
            result = AppSessionService.Global.force_refresh(record, stale_token="an-older-token")
        self.assertIsNotNone(result)
        self.assertEqual(result.access_token, "access-abc")
        idp.assert_not_called()

    def test_call_with_refresh_retries_once_with_the_fresh_token(self):
        # The reusable primitive: the first call is rejected, so it refreshes
        # and calls again with the new token, transparently.
        record, _ = self._session(expires_in=3600)
        seen: list[str] = []

        def call(token: str) -> str:
            seen.append(token)
            if len(seen) == 1:
                raise DownstreamTokenRejected()
            return "ok"

        with patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(200, _REFRESHED)):
            result = AppSessionService.Global.call_with_refresh(record, call)
        self.assertEqual(result, "ok")
        self.assertEqual(seen, ["access-abc", "access-def"])

    def test_call_with_refresh_reraises_when_dead_refresh_token(self):
        # A terminal refresh (dead refresh token -> 400) revokes the session;
        # call_with_refresh re-raises so the caller forces re-login.
        record, _ = self._session(expires_in=3600)

        def call(token: str) -> str:
            raise DownstreamTokenRejected()

        with (
            patch(
                "auth_client.services.oauth.transport.httpx.post", return_value=_Resp(400, {"error": "invalid_grant"})
            ),
            self.assertRaises(DownstreamTokenRejected),
        ):
            AppSessionService.Global.call_with_refresh(record, call)

    def test_call_with_refresh_propagates_transient_without_revoking(self):
        # A TRANSIENT IdP failure during the forced refresh (token endpoint 5xx)
        # must NOT revoke the session (a momentary blip is not a dead session):
        # it propagates AuthUpstreamUnavailable so the caller maps it to 503.
        # AuthUpstreamUnavailable subclasses AuthUpstreamError, so this guards
        # the except-ordering that a bare "refresh failed" test cannot.
        record, _ = self._session(expires_in=3600)

        def call(token: str) -> str:
            raise DownstreamTokenRejected()

        with (
            patch("auth_client.services.oauth.transport.httpx.post", return_value=_Resp(503, {})),
            self.assertRaises(AuthUpstreamUnavailable),
        ):
            AppSessionService.Global.call_with_refresh(record, call)
        record.refresh_from_db()
        self.assertIsNone(record.revoked_at)

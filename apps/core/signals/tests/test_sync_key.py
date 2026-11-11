"""The account's sync key: minted once through a person's session with
signals:read, stored encrypted, replaced by a reconnect (minted first,
swapped in one write, the old key retired after), and a refusal marks the
key refused, never its successor. The identity service is a MockTransport
at the API key proxy's httpx seam.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_sync_key
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from django.db import connection
from django.test import TestCase

from auth_client.models import AppSession
from auth_client.services.api_keys import ApiKeyRefused
from auth_client.services.oauth import TokenResponse
from auth_client.services.sessions import AppSessionService
from openbower_schema import AuthUser
from signals.models import SignalsSyncKey
from signals.services.sync_key import SYNC_KEY_NAME, SyncKeyAccountMismatch, SyncKeyService

_USER = AuthUser(id="01JQ" + "A" * 22, email="user@example.com", account_id="01JQ" + "B" * 22)
_MEMBER = AuthUser(id="01JQ" + "M" * 22, email="member@example.com", account_id=_USER.account_id)


def _key_id(n: int) -> str:
    return f"01KEY{n:021d}"


def _minted(n: int) -> dict:
    return {
        "token": f"obw_live_{n}",
        "key": {
            "id": _key_id(n),
            "name": SYNC_KEY_NAME,
            "last_four": "abcd",
            "created_at": "2026-11-11T00:00:00+00:00",
            "revoked_at": None,
            "scopes": ["profile", "signals:read"],
        },
    }


class _IdentityService:
    """Mints numbered keys and records every call. `answers` overrides the
    next answers in order; `on_mint` runs inside a mint (to stage a rival)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.minted = 0
        self.answers: list[httpx.Response] = []
        self.on_mint = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, str(request.url), body))
        if self.answers:
            return self.answers.pop(0)
        if request.method == "POST":
            self.minted += 1
            if self.on_mint is not None:
                self.on_mint()
            return httpx.Response(201, json=_minted(self.minted))
        return httpx.Response(204)


class _TokenEndpoint:
    """The identity service's token endpoint: one successful rotation."""

    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"access_token": "access-def", "refresh_token": "refresh-def", "expires_in": 3600}


def _session(user: AuthUser) -> AppSession:
    tokens = TokenResponse(access_token="access-abc", refresh_token="refresh-abc", expires_in=3600)
    session, _raw = AppSessionService.Global.create(user=user, tokens=tokens)
    return session


class SyncKeyTests(TestCase):
    def setUp(self) -> None:
        self.session = _session(_USER)
        self.hub = _IdentityService()
        patcher = patch(
            "auth_client.services.api_keys._httpx_transport", return_value=httpx.MockTransport(self.hub.handler)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.keys = SyncKeyService.for_session(self.session)

    def _mints(self) -> list[dict]:
        return [body for method, _url, body in self.hub.calls if method == "POST"]

    def _revokes(self) -> list[str]:
        return [url.rsplit("/", 1)[-1] for method, url, _body in self.hub.calls if method == "DELETE"]

    def test_ensure_mints_a_signals_read_key_once_and_reuses_it(self):
        # FAILS if ensure mints on every call: each log create would mint.
        first = self.keys.ensure(self.session)
        again = self.keys.ensure(self.session)
        self.assertEqual(first.id, again.id)
        self.assertEqual(self._mints(), [{"name": SYNC_KEY_NAME, "scopes": ["signals:read"]}])
        self.assertEqual((first.user_id, first.key_id, first.token), (_USER.id, _key_id(1), "obw_live_1"))

    def test_the_token_is_ciphertext_at_rest(self):
        # FAILS if the token is stored as plain text.
        key = self.keys.ensure(self.session)
        with connection.cursor() as cursor:
            cursor.execute("SELECT token FROM signals_signalssynckey WHERE id = %s", [str(key.id)])
            [(stored,)] = cursor.fetchall()
        self.assertNotIn("obw_live_1", stored)
        self.assertEqual(SignalsSyncKey.objects.get(id=key.id).token, "obw_live_1")

    def test_two_first_mints_end_with_one_key(self):
        # A rival lands its row while this mint is in flight: the unique row
        # decides, and the key this call minted is revoked, not left live.
        def rival() -> None:
            SignalsSyncKey.objects.create(
                account_id=_USER.account_id, user_id=_USER.id, key_id="01KEYRIVAL" + "0" * 16, token="obw_live_rival"
            )

        self.hub.on_mint = rival
        kept = self.keys.ensure(self.session)
        self.assertEqual(kept.token, "obw_live_rival")
        self.assertEqual(SignalsSyncKey.objects.count(), 1)
        self.assertEqual(self._revokes(), [_key_id(1)])

    def test_reconnect_mints_swaps_then_revokes_its_own_old_key(self):
        first = self.keys.ensure(self.session)
        replaced = self.keys.reconnect(self.session)
        self.assertEqual(replaced.key.id, first.id)
        self.assertEqual((replaced.key.token, replaced.left_live_key_id), ("obw_live_2", ""))
        # Mint, then revoke: the old key is retired only once the new one is in.
        self.assertEqual([method for method, _url, _body in self.hub.calls], ["POST", "POST", "DELETE"])
        self.assertEqual(self._revokes(), [_key_id(1)])

    def test_a_failed_mint_leaves_the_working_key_in_place(self):
        # FAILS if the old key is revoked before the new one exists: a mint
        # refused here would leave the row on a key the service killed.
        first = self.keys.ensure(self.session)
        self.hub.answers = [httpx.Response(409, json={"error": "key_limit_reached", "detail": "key limit reached"})]
        with self.assertRaises(ApiKeyRefused):
            self.keys.reconnect(self.session)
        self.assertEqual(self._revokes(), [])
        self.assertEqual(SignalsSyncKey.objects.get(id=first.id).token, "obw_live_1")

    def test_a_refreshed_session_survives_a_refused_mint(self):
        # FAILS if the mint runs inside a transaction: the refresh it forced
        # would roll back to a refresh token the service already spent.
        self.hub.answers = [
            httpx.Response(401, json={"error": "invalid_token", "detail": "expired"}),
            httpx.Response(409, json={"error": "key_limit_reached", "detail": "key limit reached"}),
        ]
        with (
            patch("auth_client.services.oauth.transport.httpx.post", return_value=_TokenEndpoint()),
            self.assertRaises(ApiKeyRefused),
        ):
            self.keys.ensure(self.session)
        self.assertEqual(AppSession.objects.get(pk=self.session.pk).refresh_token, "refresh-def")

    def test_another_members_reconnect_leaves_their_key_live_and_says_so(self):
        # The identity service revokes only an owner's keys, so a member's
        # reconnect cannot retire the minter's: it is reported, not counted
        # revoked (FAILS if the 404 a revoke would get passes as success).
        self.keys.ensure(self.session)
        member = _session(_MEMBER)
        replaced = SyncKeyService.for_session(member).reconnect(member)
        self.assertEqual((replaced.key.user_id, replaced.key.token), (_MEMBER.id, "obw_live_2"))
        self.assertEqual((replaced.left_live_key_id, replaced.left_live_user_id), (_key_id(1), _USER.id))
        self.assertEqual(self._revokes(), [])

    def test_a_refusal_marks_the_refused_key_never_its_successor(self):
        # FAILS if mark_rejected stops whatever key the account holds: a call
        # out on the old key when a reconnect lands would stop the new one.
        stale = self.keys.client()
        self.assertIsNone(stale)
        self.keys.ensure(self.session)
        stale = self.keys.client()
        self.keys.reconnect(self.session)
        self.keys.mark_rejected(stale.key_id)
        serving = self.keys.client()
        self.assertIsNotNone(serving)
        self.assertEqual(serving.key_id, _key_id(2))

    def test_a_rejected_key_stops_serving_and_ensure_replaces_it(self):
        key = self.keys.ensure(self.session)
        self.keys.mark_rejected(key.key_id)
        self.assertIsNone(self.keys.client())
        replaced = self.keys.ensure(self.session)
        self.assertEqual((replaced.token, replaced.rejected_at), ("obw_live_2", None))
        self.assertEqual(self._revokes(), [_key_id(1)])

    def test_a_session_from_another_account_is_refused(self):
        # FAILS if the account is not checked: the other account's user key
        # would become this account's feed key.
        other = _session(AuthUser(id="01JQ" + "O" * 22, email="o@example.com", account_id="01JQ" + "C" * 22))
        with self.assertRaises(SyncKeyAccountMismatch):
            self.keys.ensure(other)
        self.assertEqual(self._mints(), [])

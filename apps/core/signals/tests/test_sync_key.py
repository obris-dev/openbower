"""The account's sync key: minted once through a person's session with
signals:read, stored encrypted, replaced by a reconnect, and dropped from
use once the service refuses it. The identity service is a MockTransport
at the API key proxy's httpx seam.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_sync_key
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from django.db import connection
from django.test import TestCase

from auth_client.services.oauth import TokenResponse
from auth_client.services.sessions import AppSessionService
from openbower_schema import AuthUser
from signals.models import SignalsSyncKey
from signals.services.sync_key import SYNC_KEY_NAME, SyncKeyService

_USER = AuthUser(id="01JQ" + "A" * 22, email="user@example.com", account_id="01JQ" + "B" * 22)


def _minted(n: int) -> dict:
    return {
        "token": f"obk_live_{n}",
        "key": {
            "id": f"01KEY{n:021d}",
            "name": SYNC_KEY_NAME,
            "last_four": "abcd",
            "created_at": "2026-11-11T00:00:00+00:00",
            "revoked_at": None,
            "scopes": ["profile", "signals:read"],
        },
    }


class _IdentityService:
    """Mints numbered keys and records every call; `on_mint` runs inside a
    mint, before it answers (to stage a rival)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.minted = 0
        self.on_mint = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, str(request.url), body))
        if request.method == "POST":
            self.minted += 1
            if self.on_mint is not None:
                self.on_mint()
            return httpx.Response(201, json=_minted(self.minted))
        return httpx.Response(204)


class SyncKeyTests(TestCase):
    def setUp(self) -> None:
        tokens = TokenResponse(access_token="access-abc", refresh_token="refresh-abc", expires_in=3600)
        self.session, _raw = AppSessionService.Global.create(user=_USER, tokens=tokens)
        self.hub = _IdentityService()
        patcher = patch(
            "auth_client.services.api_keys._httpx_transport", return_value=httpx.MockTransport(self.hub.handler)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.keys = SyncKeyService(account_id=_USER.account_id)

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
        self.assertEqual((first.user_id, first.key_id, first.token), (_USER.id, _minted(1)["key"]["id"], "obk_live_1"))

    def test_the_token_is_ciphertext_at_rest(self):
        # FAILS if the token is stored as plain text.
        key = self.keys.ensure(self.session)
        with connection.cursor() as cursor:
            cursor.execute("SELECT token FROM signals_signalssynckey WHERE id = %s", [str(key.id)])
            [(stored,)] = cursor.fetchall()
        self.assertNotIn("obk_live_1", stored)
        self.assertEqual(SignalsSyncKey.objects.get(id=key.id).token, "obk_live_1")

    def test_two_first_mints_end_with_one_key(self):
        # A rival lands its row while this mint is in flight: the unique row
        # decides, and the key this call minted is revoked, not left live.
        def rival() -> None:
            SignalsSyncKey.objects.create(
                account_id=_USER.account_id, user_id=_USER.id, key_id="01KEYRIVAL" + "0" * 16, token="obk_live_rival"
            )

        self.hub.on_mint = rival
        kept = self.keys.ensure(self.session)
        self.assertEqual(kept.token, "obk_live_rival")
        self.assertEqual(SignalsSyncKey.objects.count(), 1)
        self.assertEqual(self._revokes(), [_minted(1)["key"]["id"]])

    def test_a_rejected_key_stops_serving_and_ensure_replaces_it(self):
        self.keys.ensure(self.session)
        self.assertIsNotNone(self.keys.client())
        self.keys.mark_rejected()
        self.assertIsNone(self.keys.client())
        replaced = self.keys.ensure(self.session)
        self.assertEqual((replaced.token, replaced.rejected_at), ("obk_live_2", None))
        self.assertEqual(self._revokes(), [_minted(1)["key"]["id"]])
        self.assertIsNotNone(self.keys.client())

    def test_reconnect_revokes_and_mints_in_place(self):
        first = self.keys.ensure(self.session)
        again = self.keys.reconnect(self.session)
        self.assertEqual(first.id, again.id)
        self.assertEqual(again.token, "obk_live_2")
        self.assertEqual(self._revokes(), [_minted(1)["key"]["id"]])
        self.assertEqual(SignalsSyncKey.objects.count(), 1)

    def test_an_account_without_a_key_has_no_client(self):
        self.assertIsNone(self.keys.client())

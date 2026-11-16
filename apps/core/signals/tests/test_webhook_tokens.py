"""A custom signal's webhook URL: minted at the create, read back any time
by a signed-in person, pushed to with no session and no API key, resolved
in one read, rotated (the old URL stops at once), and gone with its signal.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_webhook_tokens
"""

from __future__ import annotations

import logging
from unittest.mock import patch
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.hashing import hash_token
from openbower_schema import CUSTOM_SIGNAL_TYPE
from sheets.ingest.consumer import handle_ingest_event
from sheets.models import SheetRow
from signals.constants import WEBHOOK_TOKENS_REVOKED_KEPT, SignalsErrorCode
from signals.models import SignalSource, SourceToken
from signals.services.logs import LogNotFound, LogService
from signals.services.webhook_tokens import NoLiveToken, WebhookTokenGlobal, WebhookTokenService

from .fakes import FakeIdentity, FakeSignals, install

ACCOUNT = TEST_IDENTITY["account_id"]
_TEAMMATE = "01JQ" + "T" * 22
_CUSTOM = {"type": CUSTOM_SIGNAL_TYPE, "label": "Forum mentions", "columns": [{"label": "Title", "type": "text"}]}


class _Capture:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class WebhookTokenTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        install(self, FakeSignals(), FakeIdentity())
        # A sender: no session cookie, no API key.
        self.sender = Client()

    def _create(self) -> dict:
        resp = self.client.post(reverse("signals_index"), _CUSTOM, content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def _push(self, url: str, *, status: int) -> list:
        captured: list = []
        path = urlparse(url).path
        rows = [{"cells": {"title": "Acme mentioned"}}]
        with patch("sheets.ingest.accept.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self.sender.post(path, {"rows": rows}, content_type="application/json")
        self.assertEqual(resp.status_code, status, resp.content)
        return captured

    def test_the_create_returns_the_url_and_the_detail_never_carries_it(self):
        # FAILS if the detail carries the URL, or the hash is not what a push
        # is looked up by.
        created = self._create()
        url = created["webhook_url"]
        signal_id = created["sheet"]["id"]
        self.assertTrue(url.startswith(f"{settings.BASE_URL}/v1/signals/{signal_id}/ingest/"), url)
        token = url.rsplit("/", 1)[1]
        stored = SourceToken.objects.get(sheet_id=signal_id)
        self.assertEqual((stored.token_hash, stored.last4, stored.revoked_at), (hash_token(token), token[-4:], None))
        detail = self.client.get(reverse("signals_detail", kwargs={"id": signal_id}))
        self.assertNotIn(token, detail.content.decode())
        self.assertNotIn("webhook_url", detail.json())

    def test_a_push_to_the_url_needs_no_credential_and_is_the_creators(self):
        # FAILS if the URL needs a session or a key, or the push is carried
        # as anyone but the signal's creator.
        created = self._create()
        schema = self.sender.get(urlparse(created["webhook_url"]).path)
        self.assertEqual(schema.status_code, 200, schema.content)
        captured = self._push(created["webhook_url"], status=202)
        [event] = captured
        self.assertEqual((event.account_id, event.user_id), (ACCOUNT, TEST_IDENTITY["id"]))
        handle_ingest_event(event)
        self.assertEqual(SheetRow.objects.filter(sheet_id=created["sheet"]["id"]).count(), 1)

    def test_a_push_stays_the_creators_after_another_member_rotates(self):
        # FAILS if the push carries the token's minter rather than the
        # signal's creator: here they are different people.
        created = self._create()
        teammate = Client()
        login_session(teammate, {**TEST_IDENTITY, "id": _TEAMMATE, "email": "teammate@example.com"})
        rotated = teammate.post(reverse("signals_webhook_rotate", kwargs={"id": created["sheet"]["id"]}))
        self.assertEqual(rotated.status_code, 200, rotated.content)
        minted = SourceToken.objects.get(sheet_id=created["sheet"]["id"], revoked_at__isnull=True)
        self.assertEqual(minted.user_id, _TEAMMATE)
        [event] = self._push(rotated.json()["webhook_url"], status=202)
        self.assertEqual(event.user_id, TEST_IDENTITY["id"])

    def test_a_rotate_of_a_signal_not_in_the_account_or_deleted_is_a_404(self):
        created = self._create()
        signal_id = created["sheet"]["id"]
        outsider = Client()
        login_session(outsider, {**TEST_IDENTITY, "id": _TEAMMATE, "account_id": "01JQ" + "Z" * 22})
        foreign = outsider.post(reverse("signals_webhook_rotate", kwargs={"id": signal_id}))
        self.assertEqual(foreign.status_code, 404, foreign.content)
        self.client.delete(reverse("signals_detail", kwargs={"id": signal_id}))
        gone = self.client.post(reverse("signals_webhook_rotate", kwargs={"id": signal_id}))
        self.assertEqual(gone.status_code, 404, gone.content)

    def test_a_token_that_is_not_this_signals_live_one_is_a_404(self):
        # FAILS if any other token reaches the bus, or the answer tells a
        # wrong token from a missing signal.
        first = self._create()
        second = self._create()
        first_token = first["webhook_url"].rsplit("/", 1)[1]
        second_id = second["sheet"]["id"]
        for path in (
            reverse("signals_webhook_ingest", kwargs={"id": second_id, "token": first_token}),
            reverse("signals_webhook_ingest", kwargs={"id": second_id, "token": "not-a-token"}),
            reverse("signals_webhook_ingest", kwargs={"id": "01" + "X" * 24, "token": first_token}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self._push(path, status=404), [])
        # The schema read is the same lane: a wrong token reads nothing.
        schema = self.sender.get(reverse("signals_webhook_ingest", kwargs={"id": second_id, "token": first_token}))
        self.assertEqual(schema.status_code, 404, schema.content)
        # A session is not this lane's credential: signed in, a wrong token
        # is still a 404.
        wrong = reverse("signals_webhook_ingest", kwargs={"id": second_id, "token": "not-a-token"})
        signed_in = self.client.post(wrong, {"rows": []}, content_type="application/json")
        self.assertEqual(signed_in.status_code, 404, signed_in.content)

    def test_a_rotate_stops_the_old_url_at_once(self):
        created = self._create()
        signal_id = created["sheet"]["id"]
        rotated = self.client.post(reverse("signals_webhook_rotate", kwargs={"id": signal_id}))
        self.assertEqual(rotated.status_code, 200, rotated.content)
        self.assertNotEqual(rotated.json()["webhook_url"], created["webhook_url"])
        self._push(created["webhook_url"], status=404)
        self._push(rotated.json()["webhook_url"], status=202)
        live = SourceToken.objects.filter(sheet_id=signal_id, revoked_at__isnull=True)
        self.assertEqual(live.count(), 1)

    def test_a_rotate_keeps_only_the_newest_revoked_tokens(self):
        # FAILS if revoked tokens pile up, or the live one is pruned.
        created = self._create()
        signal_id = created["sheet"]["id"]
        rotate = reverse("signals_webhook_rotate", kwargs={"id": signal_id})
        for _ in range(WEBHOOK_TOKENS_REVOKED_KEPT + 3):
            self.client.post(rotate)
        tokens = SourceToken.objects.filter(sheet_id=signal_id)
        self.assertEqual(tokens.filter(revoked_at__isnull=False).count(), WEBHOOK_TOKENS_REVOKED_KEPT)
        last = self.client.post(rotate)
        self._push(last.json()["webhook_url"], status=202)

    def test_a_signed_in_person_reads_the_live_url_any_time(self):
        # FAILS if the URL can be copied only once, or a read hands back a
        # revoked URL after a rotate.
        created = self._create()
        webhook = reverse("signals_webhook", kwargs={"id": created["sheet"]["id"]})
        read = self.client.get(webhook)
        self.assertEqual(read.status_code, 200, read.content)
        self.assertEqual(read.json()["webhook_url"], created["webhook_url"])
        self._push(read.json()["webhook_url"], status=202)
        rotated = self.client.post(reverse("signals_webhook_rotate", kwargs={"id": created["sheet"]["id"]}))
        again = self.client.get(webhook)
        self.assertEqual(again.json()["webhook_url"], rotated.json()["webhook_url"])

    def test_only_a_session_in_the_account_reads_the_url(self):
        # FAILS if the URL reads itself (the token lane is not this route's),
        # or another account reads it.
        created = self._create()
        signal_id = created["sheet"]["id"]
        token = created["webhook_url"].rsplit("/", 1)[1]
        webhook = reverse("signals_webhook", kwargs={"id": signal_id})
        self.assertIn(self.sender.get(webhook).status_code, (401, 403))
        by_token = self.sender.get(webhook, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertNotEqual(by_token.status_code, 200)
        outsider = Client()
        login_session(outsider, {**TEST_IDENTITY, "id": _TEAMMATE, "account_id": "01JQ" + "Z" * 22})
        self.assertEqual(outsider.get(webhook).status_code, 404)

    def test_the_token_is_stored_encrypted(self):
        # FAILS if the column holds the token as sent: a database read would
        # hand out a working URL.
        created = self._create()
        token = created["webhook_url"].rsplit("/", 1)[1]
        with connection.cursor() as cursor:
            cursor.execute("select token from signals_sourcetoken where sheet_id = %s", [created["sheet"]["id"]])
            [(stored,)] = cursor.fetchall()
        self.assertNotIn(token, stored)
        self.assertEqual(SourceToken.objects.get(sheet_id=created["sheet"]["id"]).token, token)

    def test_a_url_that_no_longer_decrypts_says_to_rotate(self):
        # FAILS if a token the key can no longer decrypt is served as a URL:
        # it would 404 while the real one kept pushing.
        created = self._create()
        signal_id = created["sheet"]["id"]
        with connection.cursor() as cursor:
            cursor.execute(
                "update signals_sourcetoken set token = %s where sheet_id = %s", ["not-our-ciphertext", signal_id]
            )
        read = self.client.get(reverse("signals_webhook", kwargs={"id": signal_id}))
        self.assertEqual((read.status_code, read.json()["error"]), (409, SignalsErrorCode.WEBHOOK_UNREADABLE))
        self._push(created["webhook_url"], status=202)
        rotated = self.client.post(reverse("signals_webhook_rotate", kwargs={"id": signal_id}))
        self.assertEqual(
            self.client.get(reverse("signals_webhook", kwargs={"id": signal_id})).json()["webhook_url"],
            rotated.json()["webhook_url"],
        )

    def test_a_hosted_signal_has_no_webhook_to_rotate(self):
        hosted = LogService(account_id=ACCOUNT).create(
            owner_id=TEST_IDENTITY["id"],
            label="Seed rounds",
            signal_type="funding",
            listener_id="l1",
            autofill=True,
        )
        resp = self.client.post(reverse("signals_webhook_rotate", kwargs={"id": hosted.sheet_id}))
        self.assertEqual((resp.status_code, resp.json()["error"]), (409, SignalsErrorCode.NO_WEBHOOK))
        read = self.client.get(reverse("signals_webhook", kwargs={"id": hosted.sheet_id}))
        self.assertEqual((read.status_code, read.json()["error"]), (409, SignalsErrorCode.NO_WEBHOOK))
        self.assertFalse(SourceToken.objects.exists())

    def test_a_mint_on_a_source_gone_or_not_a_webhook_is_not_found(self):
        # FAILS if a rotate that lost a race with the delete answers 500, or
        # a hosted source can be given a webhook token.
        created = self._create()
        signal_id = created["sheet"]["id"]
        source = SignalSource.objects.get(sheet_id=signal_id)
        tokens = WebhookTokenService(account_id=ACCOUNT)
        SignalSource.objects.filter(id=source.id).delete()
        with self.assertRaises(LogNotFound):
            tokens.mint(source, user_id=TEST_IDENTITY["id"])
        hosted = LogService(account_id=ACCOUNT).create(
            owner_id=TEST_IDENTITY["id"], label="Seed rounds", signal_type="funding", listener_id="l1", autofill=True
        )
        [hosted_source] = hosted.sources
        with self.assertRaises(LogNotFound):
            tokens.mint(hosted_source, user_id=TEST_IDENTITY["id"])

    def test_a_read_that_loses_a_race_with_the_delete_is_not_found(self):
        # FAILS if the read answers 500 for a signal deleted between its two
        # reads; a source left with no live token is still said loudly.
        created = self._create()
        source = SignalSource.objects.get(sheet_id=created["sheet"]["id"])
        tokens = WebhookTokenService(account_id=ACCOUNT)
        SourceToken.objects.filter(source_id=source.id).delete()
        with self.assertRaises(NoLiveToken):
            tokens.live_token(source)
        source.delete()
        with self.assertRaises(LogNotFound) as gone:
            tokens.live_token(source)
        self.assertEqual(str(gone.exception), created["sheet"]["id"])

    def test_a_deleted_signals_tokens_go_with_it(self):
        # FAILS if a token outlives its signal: an orphan row no delete owns.
        created = self._create()
        signal_id = created["sheet"]["id"]
        self.client.post(reverse("signals_webhook_rotate", kwargs={"id": signal_id}))
        deleted = self.client.delete(reverse("signals_detail", kwargs={"id": signal_id}))
        self.assertIn(deleted.status_code, (200, 204), deleted.content)
        self.assertFalse(SourceToken.objects.filter(sheet_id=signal_id).exists())

    def test_the_lookup_is_one_read(self):
        # FAILS if a push grows a query: a webhook endpoint touches as little
        # as it can.
        created = self._create()
        token = created["webhook_url"].rsplit("/", 1)[1]
        with CaptureQueriesContext(connection) as queries:
            grant = WebhookTokenGlobal.push_target(created["sheet"]["id"], token)
        self.assertEqual(len(queries), 1)
        # The grant names the webhook source the token was minted for.
        source = SignalSource.objects.get(sheet_id=created["sheet"]["id"])
        self.assertEqual((str(grant.sheet.id), grant.source_id), (created["sheet"]["id"], str(source.id)))

    def test_a_logged_request_path_masks_the_token(self):
        # FAILS if Django's request log writes the token: a 404 is logged
        # with its path.
        created = self._create()
        path = reverse("signals_webhook_ingest", kwargs={"id": created["sheet"]["id"], "token": "leakme-secret"})
        with self.assertLogs("django.request", level="WARNING") as logged:
            self.sender.post(path, {"rows": []}, content_type="application/json")
        output = "\n".join(logged.output)
        self.assertNotIn("leakme-secret", output)
        self.assertIn("/ingest/[token]", output)

    def test_a_gunicorn_access_line_masks_the_token(self):
        # FAILS if an operator's gunicorn access log writes the token.
        with self.assertLogs("gunicorn.access", level="INFO") as logged:
            logging.getLogger("gunicorn.access").info(
                '"%s" %s', "POST /v1/signals/X/ingest/leakme-secret HTTP/1.1", 202
            )
        output = "\n".join(logged.output)
        self.assertNotIn("leakme-secret", output)
        self.assertIn("/ingest/[token]", output)

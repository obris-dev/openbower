"""A log's lifecycle against the two services it talks to (fakes at their
httpx seams): create makes the account's key (once), the listener, then the
sheet and its feed, and undoes the listener when the local write fails;
edit splits the label, the setup and autofill to their owners; delete
removes the listener first and keeps everything local if it cannot.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_log_operations
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
from django.db import DatabaseError
from django.test import TestCase

from auth_client.constants import ApiKeyRefusalCode
from auth_client.services.api_keys import ApiKeyRefused
from sheets.constants import SheetKind
from sheets.models import Sheet
from signals.log_columns import FUNDING_COLUMNS
from signals.models import LogFeed, SignalsSyncKey
from signals.operations.logs import CreateLogOperation, DeleteLogOperation, EditLogOperation, UnknownSignal
from signals.services.logs import LogService
from signals.services.signals_client import SignalsUpstreamUnavailable

from .fakes import USER, FakeIdentity, FakeSignals, install, session_for


class LogOperationTests(TestCase):
    def setUp(self) -> None:
        self.session = session_for()
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)

    def _create(self, label: str = "Robotics raises", **over):
        kwargs = {"label": label, "signal": "funding", "filter": {"stages": ["series_a"]}} | over
        return CreateLogOperation(self.session, **kwargs).run()

    def _signals_calls(self, method: str) -> list:
        return [(path, body) for m, path, body in self.signals.calls if m == method]

    def test_create_makes_the_key_the_listener_the_sheet_and_its_feed(self):
        log = self._create(prompt="Robotics companies")
        self.assertEqual(
            self._signals_calls("POST"),
            [
                (
                    "/v1/listeners",
                    {
                        "label": "Robotics raises",
                        "signal": "funding",
                        "filter": {"stages": ["series_a"]},
                        "prompt": "Robotics companies",
                    },
                )
            ],
        )
        sheet = Sheet.objects.get(id=log.local.sheet.id)
        self.assertEqual((sheet.kind, sheet.label), (SheetKind.LOG, "Robotics raises"))
        self.assertEqual([column.key for column in sheet.columns], [column.key for column in FUNDING_COLUMNS])
        self.assertTrue(all(column.immutable for column in sheet.columns))
        feed = LogFeed.objects.get(sheet_id=str(sheet.id))
        self.assertEqual((feed.listener_id, feed.signal, feed.autofill), (log.listener.id, "funding", True))
        self.assertEqual(SignalsSyncKey.objects.get().token, "obw_live_1")

    def test_a_second_log_reuses_the_accounts_key(self):
        self._create()
        self._create(label="Biotech raises")
        self.assertEqual(self.identity.minted, 1)
        self.assertEqual(LogFeed.objects.count(), 2)

    def test_a_failed_local_write_deletes_the_listener(self):
        # FAILS if the create does not undo the listener: it would match
        # and judge for a log that does not exist.
        with (
            patch("signals.services.logs.LogFeed.objects.create", side_effect=DatabaseError("down")),
            self.assertRaises(DatabaseError),
        ):
            self._create()
        self.assertEqual(self.signals.listeners, {})
        self.assertFalse(Sheet.objects.filter(kind=SheetKind.LOG).exists())

    def test_undoing_a_listener_already_gone_logs_nothing(self):
        # FAILS if create's undo reads a 404 as a listener left behind: the
        # warning would name a listener that does not exist.
        self.signals.next["DELETE"] = httpx.Response(404, json={"error": "not_found", "detail": "no such listener"})
        with (
            patch("signals.services.logs.LogFeed.objects.create", side_effect=DatabaseError("down")),
            self.assertNoLogs("signals.operations.logs", level="WARNING"),
            self.assertRaises(DatabaseError),
        ):
            self._create()

    def test_a_refused_mint_creates_nothing(self):
        # FAILS if the listener is made before the key: it would be left
        # behind by a mint the account cannot have.
        self.identity.next = [
            httpx.Response(409, json={"error": ApiKeyRefusalCode.KEY_LIMIT_REACHED, "detail": "key limit reached"})
        ]
        with self.assertRaises(ApiKeyRefused):
            self._create()
        self.assertEqual(self._signals_calls("POST"), [])
        self.assertFalse(LogFeed.objects.exists())

    def test_a_signal_with_no_log_columns_is_refused(self):
        with self.assertRaises(UnknownSignal):
            self._create(signal="hiring")
        self.assertEqual(self.signals.calls, [])

    def test_edit_sends_the_label_to_both_and_the_setup_to_the_listener(self):
        log = self._create()
        sheet_id = str(log.local.sheet.id)
        EditLogOperation(self.session, sheet_id, label="Renamed").run()
        self.assertEqual(Sheet.objects.get(id=sheet_id).label, "Renamed")
        EditLogOperation(self.session, sheet_id, filter={"stages": ["seed"]}).run()
        self.assertEqual(Sheet.objects.get(id=sheet_id).label, "Renamed")
        patches = [body for path, body in self._signals_calls("PATCH")]
        self.assertEqual(patches, [{"label": "Renamed"}, {"filter": {"stages": ["seed"]}}])

    def test_autofill_is_the_feeds_alone(self):
        log = self._create()
        edited = EditLogOperation(self.session, str(log.local.sheet.id), autofill=False).run()
        self.assertFalse(LogFeed.objects.get(id=edited.local.feed.id).autofill)
        self.assertEqual(self._signals_calls("PATCH"), [])

    def test_delete_removes_the_listener_the_feed_and_the_sheet(self):
        log = self._create()
        DeleteLogOperation(self.session, str(log.local.sheet.id)).run()
        self.assertEqual(self.signals.listeners, {})
        self.assertFalse(LogFeed.objects.exists())
        self.assertFalse(Sheet.objects.filter(id=log.local.sheet.id).exists())

    def test_a_signals_outage_deletes_nothing(self):
        log = self._create()
        self.signals.next["DELETE"] = httpx.Response(503)
        with self.assertRaises(SignalsUpstreamUnavailable):
            DeleteLogOperation(self.session, str(log.local.sheet.id)).run()
        self.assertTrue(LogFeed.objects.exists())
        self.assertTrue(Sheet.objects.filter(id=log.local.sheet.id).exists())

    def test_a_listener_already_gone_still_deletes_the_log(self):
        log = self._create()
        self.signals.listeners.clear()
        DeleteLogOperation(self.session, str(log.local.sheet.id)).run()
        self.assertFalse(Sheet.objects.filter(id=log.local.sheet.id).exists())

    def test_a_log_without_its_feed_is_a_broken_invariant(self):
        # FAILS if the index drops a feedless log: the page comes back short
        # and paging stops before the older logs.
        log = self._create()
        LogFeed.objects.filter(sheet_id=str(log.local.sheet.id)).delete()
        with self.assertRaises(KeyError):
            LogService(account_id=USER.account_id).page(after_id="", limit=10)

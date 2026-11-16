"""A custom signal's log, fed by its webhook: created with the columns a
person declares, filled by pushes through the ingest door, never polled,
and touching the signals service at no step.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_webhook_logs
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from sheets.constants import MAX_SHEET_ROWS, FillErrorCode, RowSource
from sheets.ingest.consumer import handle_ingest_event
from sheets.models import Sheet
from sheets.operations.append_rows import AppendRowsOperation, SheetNotAppendable
from sheets.rows import rows_without_external_ids
from signals.constants import LogSource, LogStatus, SignalsErrorCode
from signals.models import LogFeed, SignalsSyncKey
from signals.services.logs import LogService

from .fakes import FakeIdentity, FakeSignals, install

ACCOUNT = TEST_IDENTITY["account_id"]
_COLUMNS = [{"label": "Title", "type": "text"}, {"label": "Link", "type": "url"}, {"label": "Score", "type": "number"}]
_WEBHOOK = {"source": "webhook", "label": "Reddit mentions", "columns": _COLUMNS}


class _Capture:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class WebhookLogTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)

    def _post(self, body: dict):
        return self.client.post(reverse("logs_index"), body, content_type="application/json")

    def _create(self) -> dict:
        resp = self._post(_WEBHOOK)
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def _push(self, log_id: str, rows: list) -> None:
        captured: list = []
        with patch("sheets.views.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self.client.post(
                reverse("sheets_ingest", kwargs={"id": log_id}), {"rows": rows}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        for event in captured:
            handle_ingest_event(event)

    def _detail(self, log_id: str) -> dict:
        return self.client.get(reverse("logs_detail", kwargs={"id": log_id})).json()

    def test_a_custom_signal_is_created_waiting_with_its_declared_columns(self):
        # FAILS if a custom signal reaches the signals or identity service:
        # it has no listener and needs no sync key.
        created = self._create()
        self.assertEqual(
            (created["source"], created["status"], created["signal"], created["listener"]),
            (LogSource.WEBHOOK, LogStatus.WAITING, None, None),
        )
        self.assertIsNone(created["last_received_at"])
        columns = [(c["key"], c["type"], c["immutable"]) for c in created["sheet"]["columns"]]
        self.assertEqual(columns, [("title", "text", True), ("link", "url", True), ("score", "number", True)])
        self.assertEqual(Sheet.objects.get(id=created["sheet"]["id"]).row_source, RowSource.PUSH)
        self.assertEqual((self.signals.calls, self.identity.calls), ([], []))
        self.assertFalse(SignalsSyncKey.objects.exists())

    def test_a_push_fills_it_and_it_reads_active_with_its_last_arrival(self):
        log_id = self._create()["sheet"]["id"]
        before = timezone.now()
        self._push(log_id, [{"external_id": "r:1", "cells": {"title": "first"}}, {"title": "second"}])
        detail = self._detail(log_id)
        self.assertEqual(detail["status"], LogStatus.ACTIVE)
        self.assertGreaterEqual(datetime.datetime.fromisoformat(detail["last_received_at"]), before)
        listed = self.client.get(reverse("logs_index")).json()["items"]
        self.assertEqual([(item["source"], item["status"]) for item in listed], [("webhook", "active")])
        self.assertEqual(listed[0]["last_received_at"], detail["last_received_at"])

    def test_it_reads_full_at_the_row_cap(self):
        log_id = self._create()["sheet"]["id"]
        Sheet.objects.filter(id=log_id).update(row_count=MAX_SHEET_ROWS)
        self.assertEqual(self._detail(log_id)["status"], LogStatus.FULL)

    def test_it_takes_pushes_only_and_a_hosted_log_never_takes_one(self):
        # FAILS if the guard lets a log take any automated source: a feed
        # could write into a custom signal, or a push into a hosted one.
        sheet = Sheet.objects.get(id=self._create()["sheet"]["id"])
        rows = rows_without_external_ids([{"title": "x"}])
        with self.assertRaises(SheetNotAppendable):
            AppendRowsOperation(account_id=ACCOUNT, target_sheet=sheet, rows=rows, source=RowSource.FEED).run()
        with self.assertRaises(SheetNotAppendable):
            AppendRowsOperation(account_id=ACCOUNT, target_sheet=sheet, rows=rows, source=RowSource.MANUAL).run()
        hosted = self._post({"label": "Raises", "signal": "funding", "filter": {}}).json()["sheet"]["id"]
        resp = self.client.post(
            reverse("sheets_ingest", kwargs={"id": hosted}),
            {"rows": [{"company": "x"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)

    def test_the_poller_never_claims_it(self):
        # FAILS if the due list reads every feed: the poller would read a
        # listener a custom signal does not have.
        feed = LogFeed.objects.get(sheet_id=self._create()["sheet"]["id"])
        now = timezone.now() + datetime.timedelta(days=1)
        self.assertEqual(LogService.Global.due(now, limit=8), [])
        self.assertIsNone(LogService.Global.claim(str(feed.id), worker_id="w", now=now, lease_seconds=64))
        self.assertEqual(LogService(account_id=ACCOUNT).wake(), 0)

    def test_read_edit_and_delete_never_reach_the_signals_service(self):
        log_id = self._create()["sheet"]["id"]
        self.assertIsNone(self._detail(log_id)["listener"])
        edited = self.client.patch(
            reverse("logs_detail", kwargs={"id": log_id}), {"label": "Renamed"}, content_type="application/json"
        )
        self.assertEqual(edited.json()["sheet"]["label"], "Renamed")
        setup = self.client.patch(
            reverse("logs_detail", kwargs={"id": log_id}), {"filter": {}}, content_type="application/json"
        )
        self.assertEqual((setup.status_code, setup.json()["error"]), (400, SignalsErrorCode.SETUP_ON_WEBHOOK))
        deleted = self.client.delete(reverse("logs_detail", kwargs={"id": log_id}))
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Sheet.objects.filter(id=log_id).exists() or LogFeed.objects.filter(sheet_id=log_id).exists())
        self.assertEqual(self.signals.calls, [])

    def test_a_create_is_refused_for_what_the_other_source_takes(self):
        for case, body, status, cause in (
            ("setup on webhook", _WEBHOOK | {"signal": "funding"}, 400, "takes no signal"),
            ("no columns", {"source": "webhook", "label": "x", "columns": []}, 400, "at least one column"),
            (
                "columns on hosted",
                {"label": "x", "signal": "funding", "filter": {}, "columns": _COLUMNS},
                400,
                "takes no columns",
            ),
            ("hosted without filter", {"label": "x", "signal": "funding"}, 400, "names its signal and filter"),
        ):
            with self.subTest(case=case):
                resp = self._post(body)
                self.assertEqual(resp.status_code, status, resp.content)
                self.assertIn(cause, str(resp.json()))

    def test_declared_labels_derive_keys_by_the_one_rule(self):
        # FAILS if a custom signal's columns skip the rule every column
        # takes: two labels reading as one key would write one cell twice.
        duplicate = self._post(
            _WEBHOOK | {"columns": [{"label": "Title", "type": "text"}, {"label": "title", "type": "text"}]}
        )
        self.assertEqual((duplicate.status_code, duplicate.json()["error"]), (400, FillErrorCode.COLUMN_EXISTS))
        blank = self._post(_WEBHOOK | {"columns": [{"label": "!!!", "type": "text"}]})
        self.assertEqual((blank.status_code, blank.json()["error"]), (400, FillErrorCode.RESERVED_KEY))
        self.assertFalse(LogFeed.objects.exists())

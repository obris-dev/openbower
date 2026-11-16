"""A custom signal: a log fed by its webhook source, created with the
columns a person declares, filled by pushes to /v1/signals/{id}/ingest,
never polled, and touching the signals service at no step.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_custom_signals
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from sheets.constants import MAX_SHEET_ROWS, FillErrorCode, IngestErrorCode, RowSource
from sheets.ingest import from_wire, to_wire
from sheets.ingest.consumer import handle_ingest_event
from sheets.models import Sheet, SheetRow
from sheets.operations.append_rows import AppendRowsOperation, SheetNotAppendable
from sheets.rows import rows_without_external_ids
from signals.constants import LogStatus, SignalsErrorCode
from signals.models import SignalSource, SignalsSyncKey
from signals.services.logs import LogService
from signals.sources.registry import WEBHOOK

from .fakes import FakeIdentity, FakeSignals, install

ACCOUNT = TEST_IDENTITY["account_id"]
_COLUMNS = [{"label": "Title", "type": "text"}, {"label": "Link", "type": "url"}, {"label": "Score", "type": "number"}]
_CUSTOM = {"source": "webhook", "label": "Forum mentions", "columns": _COLUMNS}


class _Capture:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class CustomSignalTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)

    def _post(self, body: dict):
        return self.client.post(reverse("signals_index"), body, content_type="application/json")

    def _create(self) -> dict:
        resp = self._post(_CUSTOM)
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def _push(self, log_id: str, rows: list, *, status: int = 202):
        captured: list = []
        with patch("sheets.ingest.accept.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self.client.post(
                reverse("signals_ingest", kwargs={"id": log_id}), {"rows": rows}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, status, resp.content)
        for event in captured:
            handle_ingest_event(event)
        return resp

    def _detail(self, log_id: str) -> dict:
        return self.client.get(reverse("signals_detail", kwargs={"id": log_id})).json()

    def test_a_custom_signal_is_created_waiting_with_its_declared_columns(self):
        # FAILS if a custom signal reaches the signals or identity service:
        # it has no listener and needs no sync key.
        created = self._create()
        self.assertEqual(
            (created["sources"], created["status"], created["listener"], created["last_received_at"]),
            ([{"kind": WEBHOOK}], LogStatus.WAITING, None, None),
        )
        columns = [(c["key"], c["type"], c["immutable"]) for c in created["sheet"]["columns"]]
        self.assertEqual(columns, [("title", "text", True), ("link", "url", True), ("score", "number", True)])
        self.assertEqual((self.signals.calls, self.identity.calls), ([], []))
        self.assertFalse(SignalsSyncKey.objects.exists())

    def test_a_push_fills_it_and_it_reads_active_with_its_last_arrival(self):
        log_id = self._create()["sheet"]["id"]
        before = timezone.now()
        self._push(log_id, [{"external_id": "r:1", "cells": {"title": "first"}}, {"title": "second"}])
        detail = self._detail(log_id)
        self.assertEqual(detail["status"], LogStatus.ACTIVE)
        self.assertGreaterEqual(datetime.datetime.fromisoformat(detail["last_received_at"]), before)
        listed = self.client.get(reverse("signals_index")).json()["items"]
        self.assertEqual([(item["sources"], item["status"]) for item in listed], [([{"kind": WEBHOOK}], "active")])
        self.assertEqual(listed[0]["last_received_at"], detail["last_received_at"])
        self.assertEqual(SheetRow.objects.filter(sheet_id=log_id).count(), 2)

    def test_a_push_naming_an_id_it_already_holds_lands_once(self):
        log_id = self._create()["sheet"]["id"]
        row = {"external_id": "r:1", "cells": {"title": "first"}}
        self._push(log_id, [row])
        self._push(log_id, [row])
        self.assertEqual(SheetRow.objects.filter(sheet_id=log_id).count(), 1)

    def test_a_push_off_its_columns_is_refused_before_the_bus(self):
        log_id = self._create()["sheet"]["id"]
        resp = self._push(log_id, [{"score": "not a number"}], status=400)
        self.assertEqual(resp.json()["error"], IngestErrorCode.INGEST_INVALID)

    def test_it_reads_full_at_the_row_cap(self):
        log_id = self._create()["sheet"]["id"]
        Sheet.objects.filter(id=log_id).update(row_count=MAX_SHEET_ROWS)
        self.assertEqual(self._detail(log_id)["status"], LogStatus.FULL)

    def test_only_a_custom_signal_takes_a_push_and_only_by_its_own_endpoint(self):
        # FAILS if a push reaches a signal it should not: a hosted signal's
        # rows are its listener's, and a log takes no sheet push.
        log_id = self._create()["sheet"]["id"]
        hosted = self._post({"label": "Raises", "signal": "funding", "filter": {}}).json()["sheet"]["id"]
        refused = self._push(hosted, [{"company": "x"}], status=409)
        self.assertEqual(refused.json()["error"], IngestErrorCode.NOT_APPENDABLE)
        sheet_push = self.client.post(
            reverse("sheets_ingest", kwargs={"id": log_id}), {"rows": [{"title": "x"}]}, content_type="application/json"
        )
        self.assertEqual(sheet_push.status_code, 409, sheet_push.content)
        sheet = Sheet.objects.get(id=log_id)
        rows = rows_without_external_ids([{"title": "x"}])
        with self.assertRaises(SheetNotAppendable):
            AppendRowsOperation(account_id=ACCOUNT, target_sheet=sheet, rows=rows, source=RowSource.MANUAL).run()

    def test_the_poller_never_claims_it(self):
        # FAILS if a webhook source keeps a due time: the poller would claim
        # a source it has no read for.
        source = SignalSource.objects.get(sheet_id=self._create()["sheet"]["id"])
        now = timezone.now() + datetime.timedelta(days=1)
        self.assertIsNone(source.next_poll_at)
        self.assertEqual(LogService.Global.due(now, limit=8), [])
        self.assertIsNone(LogService.Global.claim(str(source.id), worker_id="w", now=now, lease_seconds=64))
        self.assertEqual(LogService(account_id=ACCOUNT).wake(), 0)

    def test_read_edit_and_delete_never_reach_the_signals_service(self):
        log_id = self._create()["sheet"]["id"]
        self.assertIsNone(self._detail(log_id)["listener"])
        edited = self.client.patch(
            reverse("signals_detail", kwargs={"id": log_id}), {"label": "Renamed"}, content_type="application/json"
        )
        self.assertEqual(edited.json()["sheet"]["label"], "Renamed")
        setup = self.client.patch(
            reverse("signals_detail", kwargs={"id": log_id}), {"filter": {}}, content_type="application/json"
        )
        self.assertEqual((setup.status_code, setup.json()["error"]), (400, SignalsErrorCode.SETUP_ON_CUSTOM_SIGNAL))
        deleted = self.client.delete(reverse("signals_detail", kwargs={"id": log_id}))
        self.assertEqual(deleted.status_code, 204)
        gone = Sheet.objects.filter(id=log_id).exists() or SignalSource.objects.filter(sheet_id=log_id).exists()
        self.assertFalse(gone)
        self.assertEqual(self.signals.calls, [])

    def test_a_create_is_refused_for_what_the_other_kind_takes(self):
        for case, body, cause in (
            ("setup on custom", _CUSTOM | {"signal": "funding"}, "takes no signal"),
            ("no columns", {"source": "webhook", "label": "x", "columns": []}, "at least one column"),
            ("columns on hosted", {"label": "x", "signal": "funding", "filter": {}, "columns": _COLUMNS}, "no columns"),
            ("hosted without filter", {"label": "x", "signal": "funding"}, "names its signal and filter"),
        ):
            with self.subTest(case=case):
                resp = self._post(body)
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(cause, str(resp.json()))

    def test_declared_labels_derive_keys_by_the_one_rule(self):
        # FAILS if a custom signal's columns skip the rule every column
        # takes: two labels reading as one key would write one cell twice.
        duplicate = self._post(
            _CUSTOM | {"columns": [{"label": "Title", "type": "text"}, {"label": "title", "type": "text"}]}
        )
        self.assertEqual((duplicate.status_code, duplicate.json()["error"]), (400, FillErrorCode.COLUMN_EXISTS))
        blank = self._post(_CUSTOM | {"columns": [{"label": "!!!", "type": "text"}]})
        self.assertEqual((blank.status_code, blank.json()["error"]), (400, FillErrorCode.RESERVED_KEY))
        self.assertFalse(SignalSource.objects.exists())


class PushSourceOnTheBusTests(SimpleTestCase):
    def test_an_event_names_its_source_and_an_old_one_reads_as_a_sheet_push(self):
        # FAILS if the bus drops the source: a signal's rows would land as a
        # sheet push, which a log refuses, and be dropped.
        wire = {
            "event_id": "e1",
            "sheet_id": "s1",
            "account_id": ACCOUNT,
            "user_id": "u1",
            "rows": [{"title": "x"}],
            "received_at": timezone.now().isoformat(),
        }
        self.assertEqual(from_wire(wire).source, RowSource.PUSH)
        signal_event = from_wire(wire | {"source": RowSource.SIGNAL})
        self.assertEqual(from_wire(to_wire(signal_event)).source, RowSource.SIGNAL)

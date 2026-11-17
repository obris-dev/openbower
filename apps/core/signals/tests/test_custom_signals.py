"""A custom signal: a log fed by its webhook source, created with the
columns a person declares, filled by pushes to /v1/signals/{id}/ingest,
never polled, and touching the signals service at no step.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_custom_signals
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.core.cache import caches
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_schema import CUSTOM_SIGNAL_TYPE
from sheets.constants import MAX_SHEET_ROWS, FillErrorCode, IngestErrorCode, RowSource
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
_CUSTOM = {"type": CUSTOM_SIGNAL_TYPE, "label": "Forum mentions", "columns": _COLUMNS}


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
            (created["sources"], created["status"], created["listener"]),
            ([{"kind": WEBHOOK}], LogStatus.WAITING, None),
        )
        columns = [(c["key"], c["type"], c["immutable"]) for c in created["sheet"]["columns"]]
        self.assertEqual(columns, [("title", "text", True), ("link", "url", True), ("score", "number", True)])
        self.assertEqual((self.signals.calls, self.identity.calls), ([], []))
        self.assertFalse(SignalsSyncKey.objects.exists())

    def test_a_push_fills_it_and_it_reads_active(self):
        log_id = self._create()["sheet"]["id"]
        self._push(log_id, [{"external_id": "r:1", "cells": {"title": "first"}}, {"title": "second"}])
        detail = self._detail(log_id)
        self.assertEqual(detail["status"], LogStatus.ACTIVE)
        listed = self.client.get(reverse("signals_index")).json()["items"]
        self.assertEqual([(item["sources"], item["status"]) for item in listed], [([{"kind": WEBHOOK}], "active")])
        self.assertEqual(SheetRow.objects.filter(sheet_id=log_id).count(), 2)

    def test_a_push_naming_an_id_it_already_holds_lands_once(self):
        log_id = self._create()["sheet"]["id"]
        row = {"external_id": "r:1", "cells": {"title": "first"}}
        self._push(log_id, [row])
        self._push(log_id, [row])
        self.assertEqual(SheetRow.objects.filter(sheet_id=log_id).count(), 1)

    def test_a_push_costs_its_session_and_one_read(self):
        # FAILS if the push endpoint reads more than the signal it lands in
        # (its status, its sync key, its listener): a webhook endpoint
        # depends on as little as it can.
        log_id = self._create()["sheet"]["id"]
        with self.assertNumQueries(1):
            sheet = LogService(account_id=ACCOUNT).push_target(log_id)
        self.assertEqual(str(sheet.id), log_id)
        with patch("sheets.ingest.accept.get_ingest_publisher", return_value=_Capture([])), self.assertNumQueries(2):
            resp = self.client.post(
                reverse("signals_ingest", kwargs={"id": log_id}),
                {"rows": [{"title": "x"}]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202, resp.content)

    def test_its_push_schema_is_read_beside_its_push(self):
        # FAILS if a sender must read a sheets path to learn what a custom
        # signal takes, or if a hosted signal offers a schema it refuses.
        log_id = self._create()["sheet"]["id"]
        schema = self.client.get(reverse("signals_ingest", kwargs={"id": log_id}))
        self.assertEqual(schema.status_code, 200, schema.content)
        self.assertEqual(
            [(column["key"], column["type"]) for column in schema.json()["columns"]],
            [("title", "text"), ("link", "url"), ("score", "number")],
        )
        hosted = self._post({"label": "Raises", "type": "funding", "filter": {}}).json()["sheet"]["id"]
        refused = self.client.get(reverse("signals_ingest", kwargs={"id": hosted}))
        self.assertEqual(refused.status_code, 409, refused.content)

    def test_a_push_off_its_columns_is_refused_before_the_bus(self):
        log_id = self._create()["sheet"]["id"]
        resp = self._push(log_id, [{"score": "not a number"}], status=400)
        self.assertEqual(resp.json()["error"], IngestErrorCode.INGEST_INVALID)

    def test_a_push_it_has_no_room_for_is_refused_before_the_bus(self):
        log_id = self._create()["sheet"]["id"]
        Sheet.objects.filter(id=log_id).update(row_count=MAX_SHEET_ROWS)
        refused = self._push(log_id, [{"title": "late"}], status=409)
        self.assertEqual(refused.json()["error"], IngestErrorCode.SHEET_FULL)

    def test_it_reads_full_at_the_row_cap(self):
        log_id = self._create()["sheet"]["id"]
        Sheet.objects.filter(id=log_id).update(row_count=MAX_SHEET_ROWS)
        self.assertEqual(self._detail(log_id)["status"], LogStatus.FULL)

    def test_only_a_custom_signal_takes_a_push_and_only_by_its_own_endpoint(self):
        # FAILS if a push reaches a signal it should not: a hosted signal's
        # rows are its listener's, and a log takes no sheet push.
        log_id = self._create()["sheet"]["id"]
        hosted = self._post({"label": "Raises", "type": "funding", "filter": {}}).json()["sheet"]["id"]
        refused = self._push(hosted, [{"company": "x"}], status=409)
        self.assertEqual(refused.json()["error"], IngestErrorCode.NOT_APPENDABLE)
        sheet_push = self.client.post(
            reverse("sheets_ingest", kwargs={"id": log_id}), {"rows": [{"title": "x"}]}, content_type="application/json"
        )
        self.assertEqual(sheet_push.status_code, 409, sheet_push.content)
        self.assertIn(f"/v1/signals/{log_id}/ingest", sheet_push.json()["detail"])
        sheet = Sheet.objects.get(id=log_id)
        rows = rows_without_external_ids([{"title": "x"}])
        with self.assertRaises(SheetNotAppendable):
            AppendRowsOperation(account_id=ACCOUNT, target_sheet=sheet, rows=rows, source=RowSource.MANUAL).run()

    def test_the_poller_never_claims_it(self):
        # FAILS if a webhook source keeps a due time: the poller would claim
        # a source it has no read for.
        log_id = self._create()["sheet"]["id"]
        source = SignalSource.objects.get(sheet_id=log_id)
        now = timezone.now() + datetime.timedelta(days=1)
        self.assertIsNone(source.next_poll_at)
        self.assertEqual(LogService.Global.due(now, limit=8), [])
        self.assertIsNone(LogService.Global.claim(str(source.id), worker_id="w", now=now, lease_seconds=64))
        logs = LogService(account_id=ACCOUNT)
        self.assertEqual(logs.wake(), 0)

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
        self.assertEqual((setup.status_code, setup.json()["error"]), (409, SignalsErrorCode.SETUP_ON_CUSTOM_SIGNAL))
        deleted = self.client.delete(reverse("signals_detail", kwargs={"id": log_id}))
        self.assertEqual(deleted.status_code, 204)
        gone = Sheet.objects.filter(id=log_id).exists() or SignalSource.objects.filter(sheet_id=log_id).exists()
        self.assertFalse(gone)
        self.assertEqual(self.signals.calls, [])

    def test_a_create_is_refused_for_what_the_other_kind_takes(self):
        for case, body, cause in (
            ("setup on custom", _CUSTOM | {"filter": {}}, "a custom signal takes no filter"),
            ("no columns", {"type": CUSTOM_SIGNAL_TYPE, "label": "x", "columns": []}, "at least one column"),
            ("columns on hosted", {"label": "x", "type": "funding", "filter": {}, "columns": _COLUMNS}, "no columns"),
            ("hosted without filter", {"label": "x", "type": "funding"}, "a funding signal names its filter"),
            ("the old type key", {"label": "x", "signal": "funding", "filter": {}}, "is `type`, not `signal`"),
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


_CORE_AUD = "openbower-core"


class _TokenInfo:
    """The hub's tokeninfo answer for an API key, at the identity fake's seam."""

    def __init__(self, claims: dict) -> None:
        self.status_code = 200
        self._claims = claims

    def json(self) -> dict:
        return self._claims


def _key_claims(**over) -> dict:
    claims = {
        "active": True,
        "sub": TEST_IDENTITY["id"],
        "account_id": ACCOUNT,
        "username": "user@example.com",
        "aud": [_CORE_AUD],
        "scope": "profile",
        "exp": 4102444800,
    }
    claims.update(over)
    return claims


@override_settings(CORE_AUDIENCE=_CORE_AUD, TOKENINFO_MISS_LIMIT_PER_MINUTE=100000)
class ApiKeyPushTests(TestCase):
    """The developer form: an account API key pushes to a custom signal and
    creates one (a sender provisioning its own), and reaches nothing else of
    the signals routes."""

    def setUp(self) -> None:
        caches["default"].clear()
        caches["tokeninfo"].clear()
        login_session(self.client)
        self.signals = FakeSignals()
        install(self, self.signals, FakeIdentity())
        created = self.client.post(reverse("signals_index"), _CUSTOM, content_type="application/json")
        self.log_id = created.json()["sheet"]["id"]
        self.key_client = Client()

    def _as_key(self, method: str, url: str, body: dict | None = None, claims: dict | None = None):
        tokeninfo = _TokenInfo(claims if claims is not None else _key_claims())
        send = getattr(self.key_client, method)
        with patch("resource_server.idp.transport.httpx.get", return_value=tokeninfo):
            return send(url, body, content_type="application/json", HTTP_AUTHORIZATION="Bearer obw_live")

    def test_a_key_pushes_as_its_account_and_user(self):
        captured: list = []
        url = reverse("signals_ingest", kwargs={"id": self.log_id})
        with patch("sheets.ingest.accept.get_ingest_publisher", return_value=_Capture(captured)):
            resp = self._as_key("post", url, {"rows": [{"title": "x"}]})
        self.assertEqual(resp.status_code, 202, resp.content)
        event = captured[0]
        self.assertEqual(
            (event.source, event.account_id, event.user_id), (RowSource.SIGNAL, ACCOUNT, TEST_IDENTITY["id"])
        )

    def test_another_account_s_key_finds_no_signal(self):
        # FAILS if the push reads a signal outside the key's account.
        url = reverse("signals_ingest", kwargs={"id": self.log_id})
        other = _key_claims(account_id="01JQ" + "Z" * 22)
        resp = self._as_key("post", url, {"rows": [{"title": "x"}]}, claims=other)
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_a_key_creates_a_custom_signal_in_its_own_account(self):
        # FAILS if a sender can't provision its signal with a key, or the
        # signal lands outside the key's account or as someone else's.
        other = "01JQ" + "Y" * 22
        body = {"type": CUSTOM_SIGNAL_TYPE, "label": "From a sender", "columns": [{"label": "Title", "type": "text"}]}
        resp = self._as_key("post", reverse("signals_index"), body, claims=_key_claims(account_id=other))
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIn("/ingest/", resp.json()["webhook_url"])
        sheet = Sheet.objects.get(id=resp.json()["sheet"]["id"])
        self.assertEqual((sheet.account_id, sheet.user_id), (other, TEST_IDENTITY["id"]))

    def test_a_key_cannot_create_a_hosted_signal(self):
        # FAILS if a key reaches a hosted create: it mints a sync key and a
        # listener through a person's session, which a key does not have.
        body = {"type": "funding", "label": "Rounds", "filter": {}}
        resp = self._as_key("post", reverse("signals_index"), body)
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, SignalsErrorCode.HOSTED_NEEDS_SESSION))
        self.assertEqual(self.signals.calls, [])

    def test_a_key_reaches_only_the_push(self):
        # FAILS if the machine lane opens beyond the push and the custom
        # create: a key could read or delete an account's signals.
        listed = self._as_key("get", reverse("signals_index"))
        self.assertEqual(listed.status_code, 401, listed.content)

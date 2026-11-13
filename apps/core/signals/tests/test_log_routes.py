"""/v1/logs and /v1/signals through real cookie auth, with the signals and
identity services as fakes at their httpx seams: a log's create, read,
edit and delete; the refusals each relays; the pass-throughs; and the
sheet doors refusing what would split a log from its listener.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_log_routes
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import httpx
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auth_client.constants import ApiKeyRefusalCode, AuthErrorCode
from common.testing import TEST_IDENTITY, login_session
from sheets.constants import LABEL_MAX_LENGTH, MAX_SHEET_ROWS, SheetErrorCode, SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.services.sheets import FolderService, SheetService
from signals.constants import KEY_LIMIT_DETAIL, PREVIEW_RUNNING_DETAIL, LogStatus, SignalsConflictCode, SignalsErrorCode
from signals.models import LogFeed, SignalsSyncKey

from .fakes import FakeIdentity, FakeSignals, install
from .test_client import _CATALOG, _EVENT, _EVENT_ID, _PREVIEW, _PREVIEW_ID

_CREATE = {"label": "Robotics raises", "signal": "funding", "filter": {"stages": ["series_a"]}}


class LogRouteTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)

    def _post(self, url: str, body: dict):
        return self.client.post(url, body, content_type="application/json")

    def _create(self, **over) -> dict:
        resp = self._post(reverse("logs_index"), _CREATE | over)
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def test_a_log_is_created_read_edited_and_deleted(self):
        created = self._create()
        log_id = created["sheet"]["id"]
        self.assertEqual(
            (created["sheet"]["kind"], created["signal"], created["sheet"]["autofill"]), ("log", "funding", True)
        )
        self.assertEqual(created["listener"]["label"], "Robotics raises")

        read = self.client.get(reverse("logs_detail", kwargs={"id": log_id}))
        self.assertEqual(read.json()["listener"]["id"], created["listener"]["id"])

        edited = self.client.patch(
            reverse("logs_detail", kwargs={"id": log_id}),
            {"label": "Renamed"},
            content_type="application/json",
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["sheet"]["label"], "Renamed")

        # Autofill is the sheet's, through the sheet doors like any sheet's.
        switched = self.client.patch(
            reverse("sheets_detail", kwargs={"id": log_id}), {"autofill": False}, content_type="application/json"
        )
        self.assertEqual(switched.status_code, 200, switched.content)
        self.assertFalse(switched.json()["autofill"])
        # FAILS if autofill is dropped quietly beside another field: the
        # label would change and the caller's autofill would not, under a 200.
        stale = self.client.patch(
            reverse("logs_detail", kwargs={"id": log_id}),
            {"label": "Renamed again", "autofill": True},
            content_type="application/json",
        )
        self.assertEqual(stale.status_code, 400, stale.content)
        self.assertIn("PATCH /v1/sheets", str(stale.json()))
        self.assertEqual(Sheet.objects.get(id=log_id).label, "Renamed")

        gone = self.client.delete(reverse("logs_detail", kwargs={"id": log_id}))
        self.assertEqual(gone.status_code, 204)
        self.assertEqual(self.client.get(reverse("logs_detail", kwargs={"id": log_id})).status_code, 404)

    def test_the_index_lists_logs_only_newest_first(self):
        first = self._create()["sheet"]["id"]
        second = self._create(label="Biotech raises")["sheet"]["id"]
        SheetService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"], label="A sheet", columns=[], origin=SheetOrigin.MANUAL
        )
        page = self.client.get(reverse("logs_index"), {"limit": "1"}).json()
        self.assertEqual([(item["sheet"]["id"], item["signal"]) for item in page["items"]], [(second, "funding")])
        rest = self.client.get(reverse("logs_index"), {"limit": "1", "after": page["next_cursor"]}).json()
        self.assertEqual([item["sheet"]["id"] for item in rest["items"]], [first])

    def test_a_key_limit_answers_in_the_apps_words(self):
        self.identity.next = [
            httpx.Response(409, json={"error": ApiKeyRefusalCode.KEY_LIMIT_REACHED, "detail": "key limit reached"})
        ]
        resp = self._post(reverse("logs_index"), _CREATE)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": SignalsErrorCode.KEY_LIMIT_REACHED, "detail": KEY_LIMIT_DETAIL})

    def test_the_services_refusal_passes_through(self):
        refusal = {"error": "limit_reached", "detail": "this account has as many listeners as it may (100)"}
        self.signals.next["POST"] = httpx.Response(409, json=refusal)
        resp = self._post(reverse("logs_index"), _CREATE)
        self.assertEqual((resp.status_code, resp.json()), (409, refusal))
        self.assertFalse(Sheet.objects.filter(kind=SheetKind.LOG).exists())

    def test_a_signal_with_no_log_columns_is_a_400(self):
        resp = self._post(reverse("logs_index"), _CREATE | {"signal": "hiring"})
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, SignalsErrorCode.UNKNOWN_SIGNAL))

    def test_an_outage_is_a_503(self):
        log_id = self._create()["sheet"]["id"]
        self.signals.next["GET"] = httpx.Response(503)
        resp = self.client.get(reverse("logs_detail", kwargs={"id": log_id}))
        self.assertEqual((resp.status_code, resp.json()["error"]), (503, SignalsErrorCode.SIGNALS_UNAVAILABLE))

    def test_an_identity_outage_names_the_identity_service(self):
        # FAILS if the sync key's mint failing reads as a signals outage:
        # whoever is on call would look at the wrong service.
        self.identity.next = [httpx.Response(503)]
        resp = self._post(reverse("logs_index"), _CREATE)
        self.assertEqual((resp.status_code, resp.json()["error"]), (503, AuthErrorCode.IDENTITY_UNAVAILABLE))
        self.assertEqual(self.signals.calls, [])

    def test_a_session_the_service_rejects_is_a_401(self):
        # FAILS if the rejection is not mapped: the web reads a 401 as "sign
        # in again", and anything else as a broken page.
        self.signals.next["GET"] = httpx.Response(401, json={"error": "not_authenticated", "detail": "token inactive"})
        with patch(
            "auth_client.services.sessions.token_auth.AppSessionService.Global.force_refresh", return_value=None
        ):
            resp = self.client.get(reverse("signals_catalog"))
        self.assertEqual((resp.status_code, resp.json()["error"]), (401, "not_authenticated"))

    def test_a_missing_scope_is_a_403(self):
        refusal = {"error": "forbidden", "detail": "token lacks the signals:write scope"}
        self.signals.next["GET"] = httpx.Response(403, json=refusal)
        resp = self.client.get(reverse("signals_catalog"))
        self.assertEqual((resp.status_code, resp.json()["error"]), (403, "permission_denied"))

    def test_a_setup_is_given_whole(self):
        log_id = self._create()["sheet"]["id"]
        resp = self.client.patch(
            reverse("logs_detail", kwargs={"id": log_id}), {"prompt": "Robots"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_the_sheet_doors_refuse_to_rename_or_delete_a_log(self):
        # FAILS if a sheet door takes a log: its listener would be left
        # matching for a log that is gone, or labelled apart from it.
        log_id = self._create()["sheet"]["id"]
        renamed = self.client.patch(
            reverse("sheets_detail", kwargs={"id": log_id}), {"label": "x"}, content_type="application/json"
        )
        deleted = self.client.delete(reverse("sheets_detail", kwargs={"id": log_id}))
        self.assertEqual((renamed.status_code, renamed.json()["error"]), (409, SheetErrorCode.LOG_ROUTE))
        self.assertEqual((deleted.status_code, deleted.json()["error"]), (409, SheetErrorCode.LOG_ROUTE))
        self.assertTrue(Sheet.objects.filter(id=log_id).exists())

    def test_another_account_cannot_reach_a_log(self):
        # FAILS if a log route reads past its account: another account's
        # person could read, rename or delete a listener not theirs.
        log_id = self._create()["sheet"]["id"]
        login_session(self.client, {**TEST_IDENTITY, "id": "01JQ" + "O" * 22, "account_id": "01JQ" + "C" * 22})
        self.signals.calls.clear()
        url = reverse("logs_detail", kwargs={"id": log_id})
        read = self.client.get(url)
        edited = self.client.patch(url, {"label": "Mine now"}, content_type="application/json")
        deleted = self.client.delete(url)
        self.assertEqual([r.status_code for r in (read, edited, deleted)], [404, 404, 404])
        self.assertEqual(self.signals.calls, [])
        self.assertEqual(Sheet.objects.get(id=log_id).label, "Robotics raises")

    def test_a_rename_and_a_move_together_are_refused_whole(self):
        # FAILS if the refusal runs after the move: a log would land in the
        # folder while the request answered 409.
        log_id = self._create()["sheet"]["id"]
        folder = FolderService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"]).create(
            label="Funding"
        )
        resp = self.client.patch(
            reverse("sheets_detail", kwargs={"id": log_id}),
            {"label": "x", "folder_id": str(folder.id)},
            content_type="application/json",
        )
        self.assertEqual((resp.status_code, resp.json()["error"]), (409, SheetErrorCode.LOG_ROUTE))
        self.assertEqual(Sheet.objects.get(id=log_id).folder_id, "")

    def test_a_log_still_moves_into_a_folder(self):
        log_id = self._create()["sheet"]["id"]
        folder = FolderService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"]).create(
            label="Funding"
        )
        moved = self.client.patch(
            reverse("sheets_detail", kwargs={"id": log_id}),
            {"folder_id": str(folder.id)},
            content_type="application/json",
        )
        self.assertEqual((moved.status_code, moved.json()["folder_id"]), (200, str(folder.id)))


class LogStatusTests(TestCase):
    """Where a log stands, derived on every read: listener gone, waiting on
    a reconnect, full, or active."""

    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        install(self, self.signals, FakeIdentity())
        resp = self.client.post(reverse("logs_index"), _CREATE, content_type="application/json")
        self.log_id = resp.json()["sheet"]["id"]
        self.url = reverse("logs_detail", kwargs={"id": self.log_id})

    def _status(self) -> str:
        page = self.client.get(reverse("logs_index")).json()
        return page["items"][0]["status"]

    def test_a_new_log_is_active(self):
        self.assertEqual(self._status(), LogStatus.ACTIVE)
        page = self.client.get(reverse("logs_index")).json()
        self.assertIsNone(page["items"][0]["last_polled_at"])

    def test_a_refused_key_waits_on_a_reconnect(self):
        # FAILS if the state ignores the key: a log would read active while
        # nothing can fill it.
        SignalsSyncKey.objects.update(rejected_at="2026-11-12T00:00:00Z")
        self.assertEqual(self._status(), LogStatus.RECONNECT)

    def test_a_log_at_the_row_cap_is_full(self):
        Sheet.objects.filter(id=self.log_id).update(row_count=MAX_SHEET_ROWS)
        self.assertEqual(self._status(), LogStatus.FULL)

    def test_the_first_state_that_holds_wins(self):
        # FAILS if the order changes: the poller skips its read on reconnect,
        # and a gone listener is final whatever else holds.
        SignalsSyncKey.objects.update(rejected_at="2026-11-12T00:00:00Z")
        Sheet.objects.filter(id=self.log_id).update(row_count=MAX_SHEET_ROWS)
        self.assertEqual(self._status(), LogStatus.RECONNECT)
        LogFeed.objects.update(listener_gone_at="2026-11-12T00:00:00Z")
        self.assertEqual(self._status(), LogStatus.LISTENER_GONE)

    def test_a_gone_listener_still_opens_the_log(self):
        # FAILS if the detail relays the listener's 404: a log with rows
        # would read as no such log, and nothing could say why.
        self.signals.listeners.clear()
        opened = self.client.get(self.url)
        self.assertEqual(opened.status_code, 200)
        self.assertEqual((opened.json()["listener"], opened.json()["status"]), (None, LogStatus.LISTENER_GONE))
        self.assertIsNotNone(LogFeed.objects.get(sheet_id=self.log_id).listener_gone_at)
        self.signals.calls.clear()
        again = self.client.get(self.url)
        self.assertEqual(again.json()["status"], LogStatus.LISTENER_GONE)
        self.assertEqual(self.signals.calls, [])

    def test_a_log_known_to_be_gone_is_refused_without_asking_the_service(self):
        LogFeed.objects.update(listener_gone_at="2026-11-12T00:00:00Z")
        self.signals.calls.clear()
        refused = self.client.patch(self.url, {"label": "Renamed"}, content_type="application/json")
        self.assertEqual((refused.status_code, refused.json()["error"]), (409, SignalsErrorCode.LISTENER_GONE))
        self.assertEqual(self.signals.calls, [])

    def test_a_log_whose_listener_is_gone_cannot_be_edited(self):
        self.signals.listeners.clear()
        refused = self.client.patch(self.url, {"label": "Renamed"}, content_type="application/json")
        self.assertEqual((refused.status_code, refused.json()["error"]), (409, SignalsErrorCode.LISTENER_GONE))
        self.assertEqual(self._status(), LogStatus.LISTENER_GONE)
        self.assertEqual(Sheet.objects.get(id=self.log_id).label, "Robotics raises")
        deleted = self.client.delete(self.url)
        self.assertEqual(deleted.status_code, 204)


class ReconnectRouteTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        self.identity = FakeIdentity()
        install(self, self.signals, self.identity)
        self.client.post(reverse("logs_index"), _CREATE, content_type="application/json")

    def test_a_reconnect_brings_a_waiting_log_back(self):
        # FAILS if reconnect does not replace the refused key: the account's
        # logs would read reconnect forever.
        SignalsSyncKey.objects.update(rejected_at="2026-11-12T00:00:00Z")
        resp = self.client.post(reverse("logs_reconnect"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {"left_live_key_id": "", "left_live_user_id": ""})
        key = SignalsSyncKey.objects.get()
        self.assertEqual((key.token, key.rejected_at), ("obw_live_2", None))
        revoked = [path for method, path, _body in self.identity.calls if method == "DELETE"]
        self.assertEqual(len(revoked), 1)
        page = self.client.get(reverse("logs_index")).json()
        self.assertEqual(page["items"][0]["status"], LogStatus.ACTIVE)

    def test_a_reconnect_makes_the_accounts_logs_due_at_once(self):
        # FAILS if the logs stay parked: a person who reconnects would wait
        # out the park before anything arrived.
        SignalsSyncKey.objects.update(rejected_at="2026-11-12T00:00:00Z")
        LogFeed.objects.update(next_poll_at=timezone.now() + datetime.timedelta(hours=1))
        self.client.post(reverse("logs_reconnect"))
        self.assertLessEqual(LogFeed.objects.get().next_poll_at, timezone.now())

    def test_a_reconnect_at_the_key_limit_answers_in_the_apps_words(self):
        SignalsSyncKey.objects.update(rejected_at="2026-11-12T00:00:00Z")
        self.identity.next = [
            httpx.Response(409, json={"error": ApiKeyRefusalCode.KEY_LIMIT_REACHED, "detail": "key limit reached"})
        ]
        resp = self.client.post(reverse("logs_reconnect"))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": SignalsErrorCode.KEY_LIMIT_REACHED, "detail": KEY_LIMIT_DETAIL})


class SignalsPassThroughTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.signals = FakeSignals()
        install(self, self.signals, FakeIdentity())

    def test_an_open_preview_answers_in_the_apps_words(self):
        # FAILS if the service's own detail passes through: it names the
        # open preview by id, which a person can do nothing with.
        running = {
            "error": "already_running",
            "detail": f"preview {_PREVIEW_ID} is still pending: wait for it, or cancel it",
            "running": {"id": _PREVIEW_ID, "status": "pending", "since": "2026-11-12T12:00:00+00:00"},
        }
        self.signals.fixed = {("POST", "/v1/previews"): httpx.Response(409, json=running)}
        resp = self.client.post(
            reverse("signals_previews"),
            {"signal": "funding", "filter": {}, "event_ids": [_EVENT_ID]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], SignalsConflictCode.ALREADY_RUNNING)
        self.assertEqual(resp.json()["detail"], PREVIEW_RUNNING_DETAIL)

    def test_the_catalog_holds_a_name_to_the_sheet_label_limit(self):
        # FAILS if the service's wider limit reaches the form: a name past
        # the sheet's label limit would pass the field and be refused on create.
        self.signals.fixed = {("GET", "/v1/signals"): httpx.Response(200, json=_CATALOG)}
        resp = self.client.get(reverse("signals_catalog"))
        self.assertGreater(_CATALOG["limits"]["label_max_length"], LABEL_MAX_LENGTH)
        self.assertEqual(resp.json()["limits"]["label_max_length"], LABEL_MAX_LENGTH)

    def test_the_catalog_events_and_previews_reach_their_upstream_routes(self):
        self.signals.fixed = {
            ("GET", "/v1/signals"): httpx.Response(200, json=_CATALOG),
            ("GET", "/v1/signals/funding/events"): httpx.Response(200, json={"items": [_EVENT], "next_cursor": None}),
            ("POST", "/v1/previews"): httpx.Response(202, json=_PREVIEW),
            ("GET", f"/v1/previews/{_PREVIEW_ID}"): httpx.Response(200, json=_PREVIEW),
            ("DELETE", f"/v1/previews/{_PREVIEW_ID}"): httpx.Response(200, json=_PREVIEW),
        }
        catalog = self.client.get(reverse("signals_catalog"))
        events = self.client.get(reverse("signals_events", kwargs={"signal": "funding"}))
        preview = self.client.post(
            reverse("signals_previews"),
            {"signal": "funding", "filter": {}, "event_ids": [_EVENT_ID]},
            content_type="application/json",
        )
        polled = self.client.get(reverse("signals_preview", kwargs={"id": _PREVIEW_ID}))
        canceled = self.client.delete(reverse("signals_preview", kwargs={"id": _PREVIEW_ID}))
        statuses = [r.status_code for r in (catalog, events, preview, polled, canceled)]
        self.assertEqual(statuses, [200, 200, 202, 200, 200])
        self.assertEqual(catalog.json()["signals"][0]["signal"], "funding")
        self.assertEqual(events.json()["items"][0]["id"], _EVENT_ID)
        sent = [(method, path) for method, path, _body in self.signals.calls]
        self.assertEqual(
            sent,
            [
                ("GET", "/v1/signals"),
                ("GET", "/v1/signals/funding/events"),
                ("POST", "/v1/previews"),
                ("GET", f"/v1/previews/{_PREVIEW_ID}"),
                ("DELETE", f"/v1/previews/{_PREVIEW_ID}"),
            ],
        )

    def test_a_preview_sends_its_setup_as_a_log_does(self):
        # FAILS if the preview passes its body through: a null threshold
        # would reach the service, where a log create leaves it out.
        self.signals.fixed = {("POST", "/v1/previews"): httpx.Response(202, json=_PREVIEW)}
        self.client.post(
            reverse("signals_previews"),
            {"signal": "funding", "filter": {}, "min_confidence": None, "event_ids": [_EVENT_ID]},
            content_type="application/json",
        )
        [(_method, _path, sent)] = self.signals.calls
        self.assertEqual(sent, {"signal": "funding", "event_ids": [_EVENT_ID], "filter": {}})

"""A sheet's autofill decides whether arriving rows start its entry actions,
whichever door brings them: a person's rows, a push, a signal's feed (the
poller's own suite). Off, rows land blank and wait for a fill; switching it
back on queues nothing for them.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_sheet_autofill
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.fields import new_ulid
from sheets.constants import SheetErrorCode, SheetKind
from sheets.ingest.consumer import handle_ingest_event
from sheets.ingest.events import IngestEvent
from sheets.models import NodeRun
from sheets.services.sheets import SheetService

from .agent_columns import sheet_with_an_ai_column

ACCOUNT = TEST_IDENTITY["account_id"]


class SheetAutofillTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.sheet = sheet_with_an_ai_column()

    def _switch(self, on: bool):
        return self.client.patch(
            reverse("sheets_detail", kwargs={"id": str(self.sheet.id)}),
            {"autofill": on},
            content_type="application/json",
        )

    def _post_rows(self, rows: list[dict[str, str]]):
        return self.client.post(
            reverse("sheets_rows", kwargs={"id": str(self.sheet.id)}), {"rows": rows}, content_type="application/json"
        )

    def _push(self, rows: list[dict[str, str]]) -> str:
        event = IngestEvent(
            event_id=new_ulid(),
            sheet_id=str(self.sheet.id),
            account_id=ACCOUNT,
            user_id=TEST_IDENTITY["id"],
            rows=rows,
            received_at=timezone.now(),
        )
        return handle_ingest_event(event)

    def _runs(self) -> int:
        return NodeRun.objects.filter(sheet_id=str(self.sheet.id)).count()

    def test_a_sheet_autofills_by_default(self):
        self.assertTrue(self.sheet.autofill)
        self.assertEqual(self._post_rows([{"company": "acme.example"}]).status_code, 201)
        self.assertEqual(self._runs(), 1)

    def test_the_switch_answers_with_the_sheet(self):
        resp = self._switch(False)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()["autofill"])
        self.sheet.refresh_from_db()
        self.assertFalse(self.sheet.autofill)

    def test_off_a_persons_rows_land_with_no_work(self):
        # FAILS if a door decides for itself: a person's rows holding values
        # would start the column the sheet said not to.
        self._switch(False)
        resp = self._post_rows([{"company": "acme.example"}])
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(self._runs(), 0)

    def test_off_a_push_lands_with_no_work(self):
        self._switch(False)
        self.assertEqual(self._push([{"company": "acme.example"}]), "applied")
        self.assertEqual(SheetService(account_id=ACCOUNT).get(str(self.sheet.id)).row_count, 1)
        self.assertEqual(self._runs(), 0)

    def test_a_logs_label_and_autofill_are_refused_whole(self):
        # FAILS if the sheet door applies the autofill half of a PATCH it
        # refuses: a log's label is its signal's, and one PATCH is one outcome.
        log = sheet_with_an_ai_column(kind=SheetKind.LOG)
        resp = self.client.patch(
            reverse("sheets_detail", kwargs={"id": str(log.id)}),
            {"label": "Renamed", "autofill": False},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], SheetErrorCode.LOG_ROUTE)
        log.refresh_from_db()
        self.assertTrue(log.autofill)

    def test_switching_back_on_queues_nothing_for_the_rows_it_missed(self):
        # FAILS if the switch backfills: rows that arrived while off wait
        # for a person's fill, and new ones start their work.
        self._switch(False)
        self._post_rows([{"company": "acme.example"}])
        self._switch(True)
        self.assertEqual(self._runs(), 0)
        self._post_rows([{"company": "globex.example"}])
        self.assertEqual(self._runs(), 1)

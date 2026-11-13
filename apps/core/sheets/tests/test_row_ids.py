"""A pushed row's own id: optional on every row, validated at the ingest
door, carried to the append, and a row whose id the sheet already holds is
skipped, so a sender that retries never doubles a row.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_row_ids
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.fields import new_ulid
from openbower_schema.sheets import ROW_ID_KEY, ROW_ID_MAX_LENGTH, derive_column_key
from sheets.constants import RowSource, SheetOrigin
from sheets.ingest.consumer import handle_ingest_event
from sheets.ingest.events import IngestEvent
from sheets.models import Sheet, SheetRow
from sheets.operations.append_rows import AppendRowsOperation
from sheets.serializers import ingest_schema_wire
from sheets.services.sheets import SheetService

ACCOUNT = TEST_IDENTITY["account_id"]


def _sheet() -> Sheet:
    return SheetService(account_id=ACCOUNT).create(
        owner_id=TEST_IDENTITY["id"],
        label="Mentions",
        columns=[{"kind": "plain", "key": "title", "label": "Title", "type": "text"}],
        origin=SheetOrigin.MANUAL,
    )


class _Capture:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class RowIdDoorTests(TestCase):
    """The door takes a row's id under ROW_ID_KEY and refuses a bad one up
    front, where the sender can still hear about it."""

    def setUp(self) -> None:
        login_session(self.client)
        self.sheet = _sheet()

    def _push(self, rows: list[dict[str, str]], capture: list | None = None):
        with patch("sheets.views.get_ingest_publisher", return_value=_Capture(capture if capture is not None else [])):
            return self.client.post(
                reverse("sheets_ingest", kwargs={"id": str(self.sheet.id)}),
                {"rows": rows},
                content_type="application/json",
            )

    def test_a_rows_id_rides_onto_the_bus(self):
        captured: list = []
        resp = self._push([{ROW_ID_KEY: "reddit:abc", "title": "Hello"}], captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [{ROW_ID_KEY: "reddit:abc", "title": "Hello"}])

    def test_a_bad_id_is_refused_before_publish(self):
        # FAILS if an id is waved through unchecked: a blank one would never
        # dedupe, and a repeated one in one push would silently drop a row.
        for rows, cause in (
            ([{ROW_ID_KEY: "  ", "title": "a"}], "blank"),
            ([{ROW_ID_KEY: "x" * (ROW_ID_MAX_LENGTH + 1), "title": "a"}], "longer than"),
            ([{ROW_ID_KEY: "same", "title": "a"}, {ROW_ID_KEY: "same", "title": "b"}], "earlier row"),
        ):
            with self.subTest(cause=cause):
                captured: list = []
                resp = self._push(rows, captured)
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(cause, resp.json()["detail"])
                self.assertEqual(captured, [])

    def test_the_schema_names_the_id_key(self):
        self.assertEqual(ingest_schema_wire(self.sheet)["row_id_key"], ROW_ID_KEY)

    def test_no_column_can_take_the_id_key(self):
        # FAILS if the derivation ever keeps a leading underscore: a column
        # keyed ROW_ID_KEY would have its value read as the row's id.
        self.assertTrue(ROW_ID_KEY.startswith("_"))
        self.assertEqual(derive_column_key(ROW_ID_KEY), "id")


class RowIdAppendTests(TestCase):
    """The append skips a row whose id the sheet holds, whichever push
    carried it; a row without an id always lands."""

    def setUp(self) -> None:
        self.sheet = _sheet()

    def _append(self, rows: list[dict[str, str]]):
        return AppendRowsOperation(account_id=ACCOUNT, target_sheet=self.sheet, rows=rows, source=RowSource.PUSH).run()

    def _titles(self) -> list[str]:
        return sorted(row.data["title"] for row in SheetRow.objects.filter(sheet_id=str(self.sheet.id)))

    def test_a_retried_row_lands_once(self):
        # FAILS if the append ignores the id: an at-least-once sender's retry
        # would double every row it resent.
        first = self._append([{ROW_ID_KEY: "reddit:1", "title": "One"}, {ROW_ID_KEY: "reddit:2", "title": "Two"}])
        again = self._append([{ROW_ID_KEY: "reddit:2", "title": "Two"}, {ROW_ID_KEY: "reddit:3", "title": "Three"}])
        self.assertEqual((first.added, again.added), (2, 1))
        self.assertEqual(self._titles(), ["One", "Three", "Two"])
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.row_count, 3)

    def test_rows_without_an_id_always_land(self):
        self._append([{"title": "Same"}])
        self._append([{"title": "Same"}])
        self.assertEqual(self._titles(), ["Same", "Same"])

    def test_the_id_is_stored_beside_the_cells_never_in_them(self):
        self._append([{ROW_ID_KEY: "hn:42", "title": "Launch"}])
        row = SheetRow.objects.get(sheet_id=str(self.sheet.id))
        self.assertEqual((row.external_id, row.data), ("hn:42", {"title": "Launch"}))

    def test_two_pushes_through_the_consumer_land_a_shared_row_once(self):
        # Distinct event ids pass the batch dedup; the row id is what holds.
        for title in ("first", "second"):
            event = IngestEvent(
                event_id=new_ulid(),
                sheet_id=str(self.sheet.id),
                account_id=ACCOUNT,
                user_id=TEST_IDENTITY["id"],
                rows=[{ROW_ID_KEY: "x:1", "title": title}],
                received_at=timezone.now(),
            )
            handle_ingest_event(event)
        self.assertEqual(self._titles(), ["first"])

"""A pushed row's external_id: a row is an envelope, {external_id?, cells},
or a plain map of cells as before; the door validates the id, the append
stores it beside the cells, and a row whose external_id the sheet already
holds is skipped, so a sender that retries never doubles a row.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_row_ids
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.fields import new_ulid
from sheets.constants import EXTERNAL_ID_MAX_LENGTH, RowSource, SheetOrigin
from sheets.ingest.consumer import handle_ingest_event
from sheets.ingest.events import IngestEvent, from_wire, to_wire
from sheets.models import Sheet, SheetRow
from sheets.operations.append_rows import AppendRowsOperation
from sheets.rows import IncomingRow
from sheets.services.sheets import SheetService

ACCOUNT = TEST_IDENTITY["account_id"]


def _sheet(*, columns: list[dict] | None = None) -> Sheet:
    return SheetService(account_id=ACCOUNT).create(
        owner_id=TEST_IDENTITY["id"],
        label="Mentions",
        columns=columns or [{"kind": "plain", "key": "title", "label": "Title", "type": "text"}],
        origin=SheetOrigin.MANUAL,
    )


class _Capture:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def publish(self, event) -> None:
        self.sink.append(event)


class EnvelopeDoorTests(TestCase):
    """The door reads either row shape and refuses a bad envelope up front,
    where the sender can still hear about it."""

    def setUp(self) -> None:
        login_session(self.client)
        self.sheet = _sheet()

    def _push(self, rows: list, sheet: Sheet | None = None, capture: list | None = None):
        target = sheet or self.sheet
        with patch("sheets.views.get_ingest_publisher", return_value=_Capture(capture if capture is not None else [])):
            return self.client.post(
                reverse("sheets_ingest", kwargs={"id": str(target.id)}),
                {"rows": rows},
                content_type="application/json",
            )

    def test_an_envelope_carries_its_id_beside_its_cells(self):
        captured: list = []
        resp = self._push([{"external_id": "reddit:abc", "cells": {"title": "Hello"}}], capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [IncomingRow(cells={"title": "Hello"}, external_id="reddit:abc")])

    def test_a_plain_row_still_pushes_with_no_id(self):
        # FAILS if the envelope broke the shape every existing caller sends.
        captured: list = []
        resp = self._push([{"title": "Hello"}], capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [IncomingRow(cells={"title": "Hello"})])

    def test_a_column_keyed_cells_is_a_plain_row_not_an_envelope(self):
        # FAILS if any row holding "cells" reads as an envelope: a sheet with
        # a column named Cells would have its pushes refused.
        sheet = _sheet(columns=[{"kind": "plain", "key": "cells", "label": "Cells", "type": "text"}])
        captured: list = []
        resp = self._push([{"cells": "battery cells"}], sheet=sheet, capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [IncomingRow(cells={"cells": "battery cells"})])

    def test_a_bad_envelope_is_refused_before_publish(self):
        # FAILS if an id or a cell is waved through unchecked: a blank id
        # never dedupes, and a NUL passes the door only for the database to
        # refuse it in the worker, which retries that push forever.
        for case, rows, cause in (
            ("blank id", [{"external_id": "  ", "cells": {"title": "a"}}], "external_id must be non-blank text"),
            ("object id", [{"external_id": {"x": 1}, "cells": {"title": "a"}}], "external_id: Not a valid string"),
            (
                "long id",
                [{"external_id": "x" * (EXTERNAL_ID_MAX_LENGTH + 1), "cells": {"title": "a"}}],
                f"no more than {EXTERNAL_ID_MAX_LENGTH} characters",
            ),
            ("NUL id", [{"external_id": "a\x00", "cells": {"title": "a"}}], "external_id: Null characters"),
            (
                "unknown field",
                [{"external_id": "a", "cells": {"title": "a"}, "extra": "x"}],
                "unknown field 'extra' (a row is {external_id?, cells})",
            ),
            ("nested cell", [{"cells": {"title": {"nested": True}}}], "column 'title': Not a valid string"),
            ("boolean cell", [{"title": True}], "column 'title': Not a valid string"),
            ("NUL envelope cell", [{"cells": {"title": "a\x00b"}}], "column 'title': Null characters"),
            ("NUL plain cell", [{"title": "a\x00b"}], "column 'title': Null characters"),
        ):
            with self.subTest(case=case):
                captured: list = []
                resp = self._push(rows, capture=captured)
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(cause, resp.json()["detail"])
                self.assertEqual(captured, [])

    def test_a_null_external_id_is_a_row_with_no_id(self):
        # FAILS if a null id is refused: many senders write an optional
        # field they have no value for as null.
        captured: list = []
        resp = self._push([{"external_id": None, "cells": {"title": "Hello"}}], capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [IncomingRow(cells={"title": "Hello"})])

    def test_a_push_repeating_an_id_is_accepted(self):
        # FAILS if the door refuses a repeat the append already skips: a
        # re-batched retry would be refused on every attempt.
        rows = [{"external_id": "same", "cells": {"title": "a"}}, {"external_id": "same", "cells": {"title": "b"}}]
        resp = self._push(rows)
        self.assertEqual(resp.status_code, 202, resp.content)

    def test_a_number_reads_as_its_digits_in_either_shape(self):
        sheet = _sheet(columns=[{"kind": "plain", "key": "score", "label": "Score", "type": "number"}])
        captured: list = []
        resp = self._push([{"score": 42}, {"cells": {"score": 7}}], sheet=sheet, capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual([row.cells for row in captured[0].rows], [{"score": "42"}, {"score": "7"}])


class ExternalIdAppendTests(TestCase):
    """The append skips a row whose external_id the sheet holds, whichever
    push carried it; a row without one always lands."""

    def setUp(self) -> None:
        self.sheet = _sheet()

    def _append(self, *titled: tuple[str, str]):
        """Rows of (title, external_id)."""
        incoming = [IncomingRow(cells={"title": title}, external_id=external_id) for title, external_id in titled]
        return AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.sheet, rows=incoming, source=RowSource.PUSH
        ).run()

    def _titles(self) -> list[str]:
        return sorted(row.data["title"] for row in SheetRow.objects.filter(sheet_id=str(self.sheet.id)))

    def test_a_retried_row_lands_once(self):
        # FAILS if the append ignores the id: an at-least-once sender's retry
        # would double every row it resent.
        first = self._append(("One", "reddit:1"), ("Two", "reddit:2"))
        again = self._append(("Two", "reddit:2"), ("Three", "reddit:3"))
        self.assertEqual((first.added, again.added), (2, 1))
        self.assertEqual(self._titles(), ["One", "Three", "Two"])
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.row_count, 3)

    def test_an_id_repeated_within_one_push_lands_its_first_row(self):
        # FAILS if a repeat within a push is refused or doubled: a sender
        # that re-batches a retried item would see that push refused on
        # every retry, or land the item twice.
        report = self._append(("first", "x:1"), ("second", "x:1"))
        self.assertEqual(report.added, 1)
        self.assertEqual(self._titles(), ["first"])

    def test_the_held_id_lookup_reads_the_external_id_index_past_100_ids(self):
        # FAILS if the lookup stops stating the index's condition: past 100
        # ids the planner cannot prove it, and the lookup scans the sheet's
        # rows while holding the sheet lock.
        rows = [IncomingRow(cells={"title": str(n)}, external_id=f"x:{n}") for n in range(150)]
        with CaptureQueriesContext(connection) as queries:
            SheetService._rows_not_held(str(self.sheet.id), rows)
        [lookup] = queries.captured_queries
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("EXPLAIN " + lookup["sql"])
            plan = " ".join(line for (line,) in cursor.fetchall())
        self.assertIn("sheet_row_external_id_uniq", plan)

    def test_rows_without_an_id_always_land(self):
        self._append(("Same", ""))
        self._append(("Same", ""))
        self.assertEqual(self._titles(), ["Same", "Same"])

    def test_the_id_is_stored_beside_the_cells(self):
        self._append(("Launch", "hn:42"))
        row = SheetRow.objects.get(sheet_id=str(self.sheet.id))
        self.assertEqual((row.external_id, row.data), ("hn:42", {"title": "Launch"}))

    def test_two_pushes_through_the_consumer_land_a_shared_row_once(self):
        # Distinct event ids pass the batch dedup; the external_id is what holds.
        for title in ("first", "second"):
            event = IngestEvent(
                event_id=new_ulid(),
                sheet_id=str(self.sheet.id),
                account_id=ACCOUNT,
                user_id=TEST_IDENTITY["id"],
                rows=[IncomingRow(cells={"title": title}, external_id="x:1")],
                received_at=timezone.now(),
            )
            handle_ingest_event(from_wire(to_wire(event)))
        self.assertEqual(self._titles(), ["first"])

    def test_the_worker_logs_the_rows_it_skipped(self):
        # FAILS if a push whose rows were all held logs only rows=0, with
        # nothing saying the sheet already had them.
        event = IngestEvent(
            event_id=new_ulid(),
            sheet_id=str(self.sheet.id),
            account_id=ACCOUNT,
            user_id=TEST_IDENTITY["id"],
            rows=[IncomingRow(cells={"title": "a"}, external_id="x:1"), IncomingRow(cells={"title": "b"})],
            received_at=timezone.now(),
        )
        self._append(("held", "x:1"))
        with self.assertLogs("sheets.ingest.consumer", level="INFO") as logs:
            handle_ingest_event(event)
        self.assertIn("rows=1 skipped=1", logs.output[-1])

    def test_an_event_from_before_ids_reads_its_bare_cells(self):
        # FAILS if the codec reads only the new row shape: an event already
        # on the bus when this deploys holds bare cell maps and would never land.
        wire = to_wire(
            IngestEvent(
                event_id=new_ulid(),
                sheet_id=str(self.sheet.id),
                account_id=ACCOUNT,
                user_id=TEST_IDENTITY["id"],
                rows=[],
                received_at=timezone.now(),
            )
        )
        wire["rows"] = [{"title": "old"}, {"cells": "battery cells"}]
        self.assertEqual(
            from_wire(wire).rows, [IncomingRow(cells={"title": "old"}), IncomingRow(cells={"cells": "battery cells"})]
        )

    def test_a_row_without_an_id_rides_the_bus_as_its_bare_cells(self):
        # FAILS if every row rides as an envelope: a worker from before ids
        # would store the envelope itself as the row's cells.
        event = IngestEvent(
            event_id=new_ulid(),
            sheet_id=str(self.sheet.id),
            account_id=ACCOUNT,
            user_id=TEST_IDENTITY["id"],
            rows=[IncomingRow(cells={"title": "a"})],
            received_at=timezone.now(),
        )
        self.assertEqual(to_wire(event)["rows"], [{"title": "a"}])

    def test_a_row_and_its_id_cross_the_bus_together(self):
        # FAILS if the codec drops the id or misreads either row shape.
        event = IngestEvent(
            event_id=new_ulid(),
            sheet_id=str(self.sheet.id),
            account_id=ACCOUNT,
            user_id=TEST_IDENTITY["id"],
            rows=[IncomingRow(cells={"title": "a"}, external_id="x:1"), IncomingRow(cells={"title": "b"})],
            received_at=timezone.now(),
        )
        self.assertEqual(from_wire(to_wire(event)).rows, event.rows)

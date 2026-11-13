"""A pushed row's external_id: a row is an envelope, {external_id?, cells},
or a plain map of cells as before; the door validates the id, the append
stores it beside the cells, and a row whose external_id the sheet already
holds is skipped, so a sender that retries never doubles a row.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_row_ids
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_kernel.fields import new_ulid
from openbower_schema.sheets import EXTERNAL_ID_MAX_LENGTH
from sheets.constants import RowSource, SheetOrigin
from sheets.ingest.consumer import handle_ingest_event
from sheets.ingest.events import IngestEvent, from_wire, to_wire
from sheets.models import Sheet, SheetRow
from sheets.operations.append_rows import AppendRowsOperation
from sheets.serializers import ingest_schema_wire
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
        self.assertEqual((captured[0].rows, captured[0].external_ids), ([{"title": "Hello"}], ["reddit:abc"]))

    def test_a_plain_row_still_pushes_with_no_id(self):
        # FAILS if the envelope broke the shape every existing caller sends.
        captured: list = []
        resp = self._push([{"title": "Hello"}], capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual((captured[0].rows, captured[0].external_ids), ([{"title": "Hello"}], []))

    def test_a_column_keyed_cells_is_a_plain_row_not_an_envelope(self):
        # FAILS if any row holding "cells" reads as an envelope: a sheet with
        # a column named Cells would have its pushes refused.
        sheet = _sheet(columns=[{"kind": "plain", "key": "cells", "label": "Cells", "type": "text"}])
        captured: list = []
        resp = self._push([{"cells": "battery cells"}], sheet=sheet, capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [{"cells": "battery cells"}])

    def test_a_bad_envelope_is_refused_before_publish(self):
        # FAILS if an id is waved through unchecked: a blank one never
        # dedupes, and a repeated one in a push silently drops a row.
        for rows, cause in (
            ([{"external_id": "  ", "cells": {"title": "a"}}], "external_id must be text"),
            ([{"external_id": 7, "cells": {"title": "a"}}], "external_id must be text"),
            ([{"external_id": "x" * (EXTERNAL_ID_MAX_LENGTH + 1), "cells": {"title": "a"}}], "longer than"),
            (
                [{"external_id": "same", "cells": {"title": "a"}}, {"external_id": "same", "cells": {"title": "b"}}],
                "earlier row",
            ),
            ([{"external_id": "a", "cells": {"title": "a"}, "extra": "x"}], "unknown field 'extra'"),
            ([{"cells": {"title": {"nested": True}}}], "must be text or a number"),
        ):
            with self.subTest(cause=cause):
                captured: list = []
                resp = self._push(rows, capture=captured)
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(cause, resp.json()["detail"])
                self.assertEqual(captured, [])

    def test_a_number_reads_as_its_digits_in_either_shape(self):
        sheet = _sheet(columns=[{"kind": "plain", "key": "score", "label": "Score", "type": "number"}])
        captured: list = []
        resp = self._push([{"score": 42}, {"cells": {"score": 7}}], sheet=sheet, capture=captured)
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(captured[0].rows, [{"score": "42"}, {"score": "7"}])

    def test_the_schema_says_rows_may_carry_an_external_id(self):
        self.assertIs(ingest_schema_wire(self.sheet)["external_id"], True)


class ExternalIdAppendTests(TestCase):
    """The append skips a row whose external_id the sheet holds, whichever
    push carried it; a row without one always lands."""

    def setUp(self) -> None:
        self.sheet = _sheet()

    def _append(self, rows: list[dict[str, str]], ids: list[str]):
        return AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.sheet, rows=rows, source=RowSource.PUSH, external_ids=ids
        ).run()

    def _titles(self) -> list[str]:
        return sorted(row.data["title"] for row in SheetRow.objects.filter(sheet_id=str(self.sheet.id)))

    def test_a_retried_row_lands_once(self):
        # FAILS if the append ignores the id: an at-least-once sender's retry
        # would double every row it resent.
        first = self._append([{"title": "One"}, {"title": "Two"}], ["reddit:1", "reddit:2"])
        again = self._append([{"title": "Two"}, {"title": "Three"}], ["reddit:2", "reddit:3"])
        self.assertEqual((first.added, again.added), (2, 1))
        self.assertEqual(self._titles(), ["One", "Three", "Two"])
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.row_count, 3)

    def test_rows_without_an_id_always_land(self):
        self._append([{"title": "Same"}], [])
        self._append([{"title": "Same"}], [""])
        self.assertEqual(self._titles(), ["Same", "Same"])

    def test_the_id_is_stored_beside_the_cells(self):
        self._append([{"title": "Launch"}], ["hn:42"])
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
                rows=[{"title": title}],
                received_at=timezone.now(),
                external_ids=["x:1"],
            )
            handle_ingest_event(from_wire(to_wire(event)))
        self.assertEqual(self._titles(), ["first"])

    def test_an_event_from_before_ids_reads_with_none(self):
        # FAILS if the codec requires the new field: an event already on the
        # bus when this deploys would fail to read and never land.
        wire = to_wire(
            IngestEvent(
                event_id=new_ulid(),
                sheet_id=str(self.sheet.id),
                account_id=ACCOUNT,
                user_id=TEST_IDENTITY["id"],
                rows=[{"title": "old"}],
                received_at=timezone.now(),
            )
        )
        del wire["external_ids"]
        self.assertEqual(from_wire(wire).external_ids, [])

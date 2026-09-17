"""The digest payload's pure shaping: the completion predicate, the
item's event id, and what an item carries.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.test import SimpleTestCase

from lists.constants import StoredCellState
from lists.models import List, ListRow
from lists.services.digest_payload import (
    EVENT_ID_HEX_LENGTH,
    build_digest_data,
    build_digest_item,
    completion_of,
    event_id_of,
)

T1 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 16, 12, 5, tzinfo=UTC)
SENT = datetime(2026, 9, 16, 13, 0, tzinfo=UTC)
LIST_ID = "01LIST" + "A" * 20
ROW_ID = "01ROW" + "A" * 21


def _row() -> ListRow:
    return ListRow(id=ROW_ID, list_id=LIST_ID, position=3, data={"company": "acme.com", "answer": "yes"})


FILLED = StoredCellState.FILLED


class CompletionTests(SimpleTestCase):
    def test_complete_when_every_waited_column_is_filled_or_a_settled_blank(self):
        records = {"a": (FILLED, T1), "b": (StoredCellState.NO_EVIDENCE, T2)}
        self.assertEqual(completion_of(records, ["a", "b"]), T2)

    def test_incomplete_while_any_waited_column_is_unattempted(self):
        # Absence means never attempted, whatever the others say.
        self.assertIsNone(completion_of({"a": (FILLED, T1)}, ["a", "b"]))
        self.assertIsNone(completion_of({}, ["a"]))

    def test_incomplete_while_any_waited_column_ended_in_a_retryable_failure(self):
        # A timeout or a missing tool is not an answer and not a reason:
        # the refill re-runs it, so the row is not complete yet.
        for state in (StoredCellState.TRANSIENT, StoredCellState.MODEL_ERROR, StoredCellState.TOOL_UNAVAILABLE):
            with self.subTest(state=state):
                self.assertIsNone(completion_of({"a": (FILLED, T1), "b": (state, T2)}, ["a", "b"]))


class ItemTests(SimpleTestCase):
    def test_event_id_is_derived_from_scope_row_and_completion_time(self):
        item = build_digest_item(
            scope=LIST_ID,
            row=_row(),
            cells={"company": "acme.com"},
            states={"answer": "filled"},
            completed_at=T2,
            sent_at=SENT,
            test=False,
        )
        self.assertEqual(item.event_id, event_id_of(scope=LIST_ID, row_id=ROW_ID, stamp=T2.isoformat(), test=False))
        self.assertRegex(item.event_id, rf"^[0-9a-f]{{{EVENT_ID_HEX_LENGTH}}}$")
        self.assertEqual(item.completed_at, T2.isoformat())
        self.assertEqual(item.row_id, ROW_ID)
        self.assertEqual(item.position, 3)
        # Cells are exactly what the caller chose, never the whole row.
        self.assertEqual(item.cells, {"company": "acme.com"})
        self.assertEqual(item.states, {"answer": "filled"})

    def test_the_same_completion_yields_the_same_id_and_a_new_completion_a_new_one(self):
        def item(**overrides):
            base = {
                "scope": LIST_ID,
                "row": _row(),
                "cells": {},
                "states": {},
                "completed_at": T2,
                "sent_at": SENT,
                "test": False,
            }
            return build_digest_item(**{**base, **overrides})

        first = item()
        # A redelivery rebuilt from the same state dedups.
        self.assertEqual(first.event_id, item().event_id)
        # A re-fill moves the completion, so it is a new event.
        self.assertNotEqual(first.event_id, item(completed_at=T1).event_id)
        # Another scope (a second webhook node once one exists) never
        # shares ids with this one.
        self.assertNotEqual(first.event_id, item(scope=ROW_ID).event_id)
        # A test send of the same completion is not the live event: a
        # receiver ignoring test data must not have already seen its id.
        self.assertNotEqual(first.event_id, item(test=True).event_id)

    def test_an_incomplete_sample_ids_on_the_send_time_and_carries_no_completion(self):
        item = build_digest_item(
            scope=LIST_ID, row=_row(), cells={}, states={}, completed_at=None, sent_at=SENT, test=True
        )
        self.assertEqual(item.event_id, event_id_of(scope=LIST_ID, row_id=ROW_ID, stamp=SENT.isoformat(), test=True))
        self.assertIsNone(item.completed_at)

    def test_data_names_the_sheet_and_the_waited_columns(self):
        target = List(id=LIST_ID, label="Prospects", columns=[])
        item = build_digest_item(
            scope=LIST_ID, row=_row(), cells={}, states={}, completed_at=None, sent_at=SENT, test=True
        )
        data = build_digest_data(target, waited_on=["answer"], items=[item])
        self.assertEqual(data.type, "digest")
        self.assertEqual(data.sheet.id, LIST_ID)
        self.assertEqual(data.sheet.label, "Prospects")
        self.assertEqual(data.waited_on, ["answer"])
        self.assertEqual(len(data.items), 1)

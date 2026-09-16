"""The digest payload's pure shaping: the completion predicate, the
item key, and what an item carries.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.test import SimpleTestCase

from lists.models import List, ListRow
from lists.services.digest_payload import build_digest_data, build_digest_item, completion_of

T1 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 16, 12, 5, tzinfo=UTC)
SENT = datetime(2026, 9, 16, 13, 0, tzinfo=UTC)
LIST_ID = "01LIST" + "A" * 20
ROW_ID = "01ROW" + "A" * 21


def _row() -> ListRow:
    return ListRow(id=ROW_ID, list_id=LIST_ID, position=3, data={"company": "acme.com", "answer": "yes"})


class CompletionTests(SimpleTestCase):
    def test_complete_when_every_waited_column_has_a_record(self):
        self.assertEqual(completion_of({"a": T1, "b": T2}, ["a", "b"]), T2)

    def test_incomplete_while_any_waited_column_is_unattempted(self):
        # Absence means never attempted, whatever the others say.
        self.assertIsNone(completion_of({"a": T1}, ["a", "b"]))
        self.assertIsNone(completion_of({}, ["a"]))


class ItemTests(SimpleTestCase):
    def test_key_uses_the_completion_time_and_the_scope(self):
        item = build_digest_item(
            scope=LIST_ID,
            row=_row(),
            cells={"company": "acme.com"},
            states={"answer": "filled"},
            completed_at=T2,
            sent_at=SENT,
        )
        self.assertEqual(item.key, f"{LIST_ID}:{ROW_ID}:{T2.isoformat()}")
        self.assertEqual(item.completed_at, T2.isoformat())
        self.assertEqual(item.row_id, ROW_ID)
        self.assertEqual(item.position, 3)
        # Cells are exactly what the caller chose, never the whole row.
        self.assertEqual(item.cells, {"company": "acme.com"})
        self.assertEqual(item.states, {"answer": "filled"})

    def test_an_incomplete_sample_keys_on_the_send_time_and_carries_no_completion(self):
        item = build_digest_item(scope=LIST_ID, row=_row(), cells={}, states={}, completed_at=None, sent_at=SENT)
        self.assertEqual(item.key, f"{LIST_ID}:{ROW_ID}:{SENT.isoformat()}")
        self.assertIsNone(item.completed_at)

    def test_data_names_the_sheet_and_the_waited_columns(self):
        target = List(id=LIST_ID, label="Prospects", columns=[])
        item = build_digest_item(scope=LIST_ID, row=_row(), cells={}, states={}, completed_at=None, sent_at=SENT)
        data = build_digest_data(target, column_keys=["answer"], items=[item])
        self.assertEqual(data.type, "digest")
        self.assertEqual(data.sheet.id, LIST_ID)
        self.assertEqual(data.sheet.label, "Prospects")
        self.assertEqual(data.column_keys, ["answer"])
        self.assertEqual(len(data.items), 1)

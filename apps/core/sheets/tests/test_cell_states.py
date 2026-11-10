"""The cell-state read seam: every iterator is scoped to the account it
was built for, which is the module's entire reason to exist.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.test import TestCase

from common.testing import TEST_IDENTITY
from sheets.constants import CellSource, StoredCellState
from sheets.models import SheetCellState
from sheets.services import cell_truth
from sheets.services.cell_states import CellStateService
from sheets.services.sheets import SheetService

COLUMNS = [{"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": "01ND" + "A" * 22}]


class CellStateScopingTests(TestCase):
    def setUp(self) -> None:
        self.account_id = TEST_IDENTITY["account_id"]
        sheets = SheetService(account_id=self.account_id)
        self.sheet = sheets.create(owner_id=TEST_IDENTITY["id"], label="Prospects", columns=COLUMNS, origin="manual")
        [self.row] = sheets.add_rows(self.sheet, [{"company": "acme.com"}])
        cell_truth.write(
            account_id=self.account_id,
            sheet_id=str(self.sheet.id),
            row_id=str(self.row.id),
            fill_run_id=None,
            states={"answer": StoredCellState.FILLED},
            tools={},
            source=CellSource.NODE,
        )
        self.mine = CellStateService(account_id=self.account_id)
        self.theirs = CellStateService(account_id="01ACCT" + "Z" * 20)

    def _reads(self, service: CellStateService) -> list[int]:
        sheet_id = str(self.sheet.id)
        row_ids = [str(self.row.id)]
        return [
            len(list(service.iter_states(sheet_id, row_id=str(self.row.id), column_keys=["answer"]))),
            len(list(service.iter_recorded(sheet_id, row_ids=row_ids, column_keys=["answer"]))),
            len(list(service.iter_counts_by_column(sheet_id, column_keys=["answer"]))),
        ]

    def test_every_iterator_sees_the_accounts_records(self):
        self.assertEqual(self._reads(self.mine), [1, 0, 1])
        # iter_recorded drops a clean filled record by design; the other
        # two see it.

    def test_another_account_sees_nothing_through_any_iterator(self):
        self.assertEqual(self._reads(self.theirs), [0, 0, 0])


class CellSourceTests(TestCase):
    def test_a_fill_lands_as_the_fill_source_and_a_later_writer_re_attributes(self):
        sheets = SheetService(account_id=TEST_IDENTITY["account_id"])
        sheet = sheets.create(owner_id=TEST_IDENTITY["id"], label="Prospects", columns=COLUMNS, origin="manual")
        [row] = sheets.add_rows(sheet, [{"company": "acme.com"}])
        write = {"account_id": TEST_IDENTITY["account_id"], "list_id": str(sheet.id), "row_id": str(row.id)}
        cell_truth.write(
            **write,
            fill_run_id=None,
            states={"answer": StoredCellState.FILLED},
            tools={},
            source=CellSource.NODE,
        )
        record = SheetCellState.objects.get(sheet_id=str(sheet.id), column_key="answer")
        self.assertEqual(record.source, CellSource.NODE)
        cell_truth.write(
            **write,
            fill_run_id=None,
            states={"answer": StoredCellState.FILLED},
            tools={},
            source=CellSource.MANUAL,
        )
        record.refresh_from_db()
        self.assertEqual(record.source, CellSource.MANUAL)
        # And a fill writing over it takes the cell back.
        cell_truth.write(
            **write,
            fill_run_id=None,
            states={"answer": StoredCellState.FILLED},
            tools={},
            source=CellSource.NODE,
        )
        record.refresh_from_db()
        self.assertEqual(record.source, CellSource.NODE)

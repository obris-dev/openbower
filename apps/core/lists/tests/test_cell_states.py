"""The cell-state read seam: every iterator is scoped to the account it
was built for, which is the module's entire reason to exist.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.test import TestCase

from common.testing import TEST_IDENTITY
from lists.constants import StoredCellState
from lists.services import cell_truth
from lists.services.cell_states import CellStateService
from lists.services.lists import ListService

COLUMNS = [{"key": "answer", "label": "Answer", "type": "text", "fill": {"node_id": "01ND" + "A" * 22}}]


class CellStateScopingTests(TestCase):
    def setUp(self) -> None:
        self.account_id = TEST_IDENTITY["account_id"]
        lists = ListService(account_id=self.account_id)
        self.sheet = lists.create(owner_id=TEST_IDENTITY["id"], label="Prospects", columns=COLUMNS, origin="manual")
        [self.row] = lists.add_rows(self.sheet, [{"company": "acme.com"}])
        cell_truth.write(
            account_id=self.account_id,
            list_id=str(self.sheet.id),
            row_id=str(self.row.id),
            fill_run_id=None,
            config_fingerprint="fp",
            states={"answer": StoredCellState.FILLED},
            tools={},
        )
        self.mine = CellStateService(account_id=self.account_id)
        self.theirs = CellStateService(account_id="01ACCT" + "Z" * 20)

    def _reads(self, service: CellStateService) -> list[int]:
        list_id = str(self.sheet.id)
        row_ids = [str(self.row.id)]
        return [
            len(list(service.iter_states(list_id, row_id=str(self.row.id), column_keys=["answer"]))),
            len(list(service.iter_recorded(list_id, row_ids=row_ids, column_keys=["answer"]))),
            len(list(service.iter_counts_by_column(list_id, column_keys=["answer"]))),
            len(list(service.iter_settled(list_id, row_ids=row_ids, column_keys=["answer"], fingerprint="fp"))),
        ]

    def test_every_iterator_sees_the_accounts_records(self):
        self.assertEqual(self._reads(self.mine), [1, 0, 1, 1])
        # iter_recorded drops a clean filled record by design; the other
        # three see it.

    def test_another_account_sees_nothing_through_any_iterator(self):
        self.assertEqual(self._reads(self.theirs), [0, 0, 0, 0])

"""How a log is written: its base columns are immutable (a person never
edits their cells, and they cannot be deleted), while a person's own
columns on the same log stay writable.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_log_writes
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from openbower_schema.sheets import PlainColumn
from sheets.constants import CellErrorCode, FillErrorCode, SheetKind, SheetOrigin
from sheets.operations.append_rows import AppendRowsOperation
from sheets.services.sheets import SheetService

ACCOUNT = TEST_IDENTITY["account_id"]


class ImmutableColumnTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.sheets = SheetService(account_id=ACCOUNT)
        self.log = self.sheets.create(
            owner_id=TEST_IDENTITY["id"],
            label="Funding log",
            columns=[
                PlainColumn(key="company", label="Company", type="text", immutable=True),
                PlainColumn(key="notes", label="Notes", type="text"),
            ],
            origin=SheetOrigin.MANUAL,
            kind=SheetKind.LOG,
        )
        report = AppendRowsOperation(
            account_id=ACCOUNT, target_sheet=self.log, rows=[{"company": "acme.example"}]
        ).run()
        self.row_id = str(report.created[0].id)

    def _patch_cells(self, data: dict[str, str]):
        return self.client.patch(
            reverse("sheets_row", kwargs={"id": str(self.log.id), "row_id": self.row_id}),
            {"data": data},
            content_type="application/json",
        )

    def test_a_person_cannot_edit_an_immutable_cell(self):
        # FAILS if set_cells ignores the flag: the arrived value would change.
        resp = self._patch_cells({"company": "renamed.example"})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], CellErrorCode.COLUMN_NOT_WRITABLE)
        self.assertIn("cannot be edited", resp.json()["detail"])
        self.assertEqual(self.sheets.row(self.log, self.row_id).data["company"], "acme.example")

    def test_a_persons_own_column_on_a_log_stays_writable(self):
        resp = self._patch_cells({"notes": "call next week"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.sheets.row(self.log, self.row_id).data["notes"], "call next week")

    def test_an_immutable_column_cannot_be_deleted(self):
        # FAILS if the delete ignores the flag: the column and its values go.
        resp = self.client.delete(reverse("sheets_column_detail", kwargs={"id": str(self.log.id), "key": "company"}))
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], FillErrorCode.COLUMN_IMMUTABLE)
        self.log.refresh_from_db()
        self.assertEqual([column.key for column in self.log.columns], ["company", "notes"])

    def test_an_immutable_column_can_be_renamed_and_reordered(self):
        renamed = self.client.patch(
            reverse("sheets_column_detail", kwargs={"id": str(self.log.id), "key": "company"}),
            {"label": "Company name"},
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)
        reordered = self.client.patch(
            reverse("sheets_columns_order", kwargs={"id": str(self.log.id)}),
            {"keys": ["notes", "company"]},
            content_type="application/json",
        )
        self.assertEqual(reordered.status_code, 200, reordered.content)
        self.log.refresh_from_db()
        self.assertEqual(
            [(c.key, c.label, c.immutable) for c in self.log.columns][1], ("company", "Company name", True)
        )

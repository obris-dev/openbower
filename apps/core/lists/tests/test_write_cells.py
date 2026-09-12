"""The one cell writer: write-if-blank under the ROW lock, the clamp,
the per-type shape validators, and machine blanks writing nothing.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_write_cells
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from lists.constants import CELL_MAX_LENGTH, ColumnType, ListOrigin
from lists.models import ListRow
from lists.services.lists import ListNotFound, ListService, RowNotFound
from openbower_schema.cell_types import CellTypeMismatch, validate_cell

_COLUMNS = [
    {"key": "name", "label": "Name", "type": "text"},
    {"key": "employees", "label": "Employees", "type": "number"},
    {"key": "revenue", "label": "Revenue", "type": "currency"},
    {"key": "founded", "label": "Founded", "type": "date"},
    {"key": "domain", "label": "Domain", "type": "url"},
]


def _service(account="01AC" + "A" * 22, user="01US" + "A" * 22) -> ListService:
    return ListService(account_id=account)


def _sheet(service: ListService, rows: list[dict[str, str]]):
    target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
    service.add_rows(target, rows)
    return target, service.rows_page(target, after_position=0, limit=len(rows))


class WriteIfBlankTests(TestCase):
    def test_blank_cell_written(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.write_cells(str(target.id), str(row.id), {"employees": "1,200"})
        self.assertEqual(result.written, ("employees",))
        self.assertEqual(result.occupied, ())
        self.assertEqual(result.mismatched, ())
        row.refresh_from_db()
        self.assertEqual(row.data["employees"], "1200")

    def test_occupied_cell_untouched(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.write_cells(str(target.id), str(row.id), {"name": "Machine Name"})
        self.assertEqual(result.occupied, ("name",))
        self.assertEqual(result.written, ())
        row.refresh_from_db()
        self.assertEqual(row.data["name"], "Acme")  # the user's value survives

    def test_clamp_at_cell_max_length(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": ""}])
        result = service.write_cells(str(target.id), str(row.id), {"name": "x" * (CELL_MAX_LENGTH + 8)})
        self.assertEqual(result.written, ("name",))
        row.refresh_from_db()
        self.assertEqual(len(row.data["name"]), CELL_MAX_LENGTH)

    def test_type_mismatch_writes_nothing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.write_cells(str(target.id), str(row.id), {"employees": "around fifty"})
        self.assertEqual(result.written, ())
        self.assertEqual([m.key for m in result.mismatched], ["employees"])
        self.assertIn("around fifty", result.mismatched[0].why)
        row.refresh_from_db()
        self.assertNotIn("employees", row.data)

    def test_blank_values_skip_entirely(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        before = dict(ListRow.objects.get(id=row.id).data)
        result = service.write_cells(str(target.id), str(row.id), {"employees": "", "revenue": "   "})
        self.assertEqual(result, ((), (), ()))
        row.refresh_from_db()
        self.assertEqual(row.data, before)

    def test_mixed_dict_partial_outcomes(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"name": "Machine Name", "employees": "42", "founded": "next spring", "domain": ""},
        )
        self.assertEqual(result.written, ("employees",))
        self.assertEqual(result.occupied, ("name",))
        self.assertEqual([m.key for m in result.mismatched], ["founded"])
        row.refresh_from_db()
        self.assertEqual(row.data["name"], "Acme")
        self.assertEqual(row.data["employees"], "42")
        self.assertNotIn("founded", row.data)
        self.assertNotIn("domain", row.data)

    def test_key_without_a_column_writes_untouched(self):
        # No column, no shape rule: the value stores as given, like any
        # text cell (key matching and mapping happen upstream).
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.write_cells(str(target.id), str(row.id), {"note": "Series B, 2024"})
        self.assertEqual(result.written, ("note",))
        row.refresh_from_db()
        self.assertEqual(row.data["note"], "Series B, 2024")

    def test_unknown_row_and_list_read_as_missing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        other = service.create(owner_id="01US" + "A" * 22, label="Other", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        with self.assertRaises(RowNotFound):
            service.write_cells(str(target.id), "01RW" + "Z" * 22, {"name": "x"})
        with self.assertRaises(RowNotFound):  # a row outside the list is missing too
            service.write_cells(str(other.id), str(row.id), {"name": "x"})
        with self.assertRaises(ListNotFound):
            service.write_cells("01LS" + "Z" * 22, str(row.id), {"name": "x"})
        with self.assertRaises(ListNotFound):  # cross-tenant reads as missing
            _service(account="01AC" + "Z" * 22).write_cells(str(target.id), str(row.id), {"name": "x"})


class NumberValidatorTests(TestCase):
    def test_accepts_and_normalizes(self):
        for raw, stored in [
            ("1234", "1234"),
            ("1,234,567", "1234567"),
            ("-12.5", "-12.5"),
            ("+3.14", "+3.14"),
            (" 42 ", "42"),
            ("1,234.50", "1234.50"),
        ]:
            self.assertEqual(validate_cell(ColumnType.NUMBER, raw), stored)

    def test_refuses_non_numbers(self):
        for raw in ["around fifty", "12a", "1.2.3", "1,23", "1 234", "", "1e5"]:
            with self.assertRaises(CellTypeMismatch):
                validate_cell(ColumnType.NUMBER, raw)

    def test_refusal_carries_key_and_why(self):
        with self.assertRaises(CellTypeMismatch) as caught:
            validate_cell(ColumnType.NUMBER, "12a", key="employees")
        self.assertEqual(caught.exception.key, "employees")
        self.assertIn("12a", caught.exception.why)


class CurrencyValidatorTests(TestCase):
    def test_accepts_and_normalizes(self):
        for raw, stored in [
            ("$1,234.50", "$1234.50"),
            ("€ 42", "€42"),
            ("£999", "£999"),
            ("12.00", "12.00"),
        ]:
            self.assertEqual(validate_cell(ColumnType.CURRENCY, raw), stored)

    def test_refuses_non_amounts(self):
        for raw in ["$", "$$5", "USD 100", "100 USD", "5$", "$1,23"]:
            with self.assertRaises(CellTypeMismatch):
                validate_cell(ColumnType.CURRENCY, raw)


class DateValidatorTests(TestCase):
    def test_accepts_year_first_and_normalizes_to_iso(self):
        for raw, stored in [
            ("2024-08-21", "2024-08-21"),
            ("2024/8/3", "2024-08-03"),
            ("2024.12.01", "2024-12-01"),
        ]:
            self.assertEqual(validate_cell(ColumnType.DATE, raw), stored)

    def test_refuses_ambiguous_forms(self):
        # 03/04/2024 reads two ways; refusing beats storing a guess.
        for raw in ["03/04/2024", "21-08-2024", "August 21, 2024", "2024-08/21", "someday"]:
            with self.assertRaises(CellTypeMismatch):
                validate_cell(ColumnType.DATE, raw)

    def test_refuses_impossible_calendar_dates(self):
        for raw in ["2024-13-01", "2024-02-30"]:
            with self.assertRaises(CellTypeMismatch):
                validate_cell(ColumnType.DATE, raw)


class PassThroughTests(TestCase):
    def test_untyped_shapes_pass_untouched(self):
        for column_type in [ColumnType.TEXT, ColumnType.URL, ColumnType.EMAIL]:
            self.assertEqual(validate_cell(column_type, "  as written  "), "  as written  ")


class LockGranularityTests(TransactionTestCase):
    """The hazard write_cells guards is a read-modify-write of ONE
    row's json, so the lock is on THAT ROW.

    It used to take `select_for_update` on the LIST, and ListRow was
    never locked anywhere in the codebase. That collapsed the worker's
    64 wide pool to concurrency 1 per sheet at the terminal write, and
    made two unrelated AI columns filling one sheet contend for
    nothing."""

    def test_the_write_locks_the_row_and_not_the_list(self) -> None:
        lists = ListService(account_id="01AC" + "A" * 22)
        sheet = lists.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        lists.add_rows(sheet, [{"name": "acme"}])
        row = ListRow.objects.get(list_id=str(sheet.id))
        with CaptureQueriesContext(connection) as captured:
            lists.write_cells(str(sheet.id), str(row.id), {"employees": "12"})
        locked = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        self.assertTrue(locked, "the write took no row lock at all")
        for sql in locked:
            self.assertIn("lists_listrow", sql.lower())
            self.assertNotIn('lists_list"', sql.lower())

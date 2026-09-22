"""The one cell writer: write-if-blank under the ROW lock, the clamp,
the per-type shape validators, and machine blanks writing nothing.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_write_cells
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from lists.constants import CELL_MAX_LENGTH, CellSource, ColumnType, ListOrigin
from lists.models import ListRow
from lists.services.cell_truth import CellTruth
from lists.services.lists import ListNotFound, ListService, RowNotFound
from openbower_schema.cell_types import CellTypeMismatch, normalize_row, validate_cell

_COLUMNS = [
    {"kind": "plain", "key": "name", "label": "Name", "type": "text"},
    {"kind": "plain", "key": "employees", "label": "Employees", "type": "number"},
    {"kind": "plain", "key": "revenue", "label": "Revenue", "type": "currency"},
    {"kind": "plain", "key": "founded", "label": "Founded", "type": "date"},
    {"kind": "plain", "key": "domain", "label": "Domain", "type": "url"},
]


def _service(account="01AC" + "A" * 22, user="01US" + "A" * 22) -> ListService:
    return ListService(account_id=account)


def _sheet(service: ListService, rows: list[dict[str, str]]):
    target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
    service.add_rows(target, rows)
    return target, service.rows_page(target, limit=len(rows))


class WriteIfBlankTests(TestCase):
    def test_blank_cell_written(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"employees": "1,200"},
            column_keys=("employees",),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result.written, ("employees",))
        self.assertEqual(result.occupied, ())
        self.assertEqual(result.mismatched, ())
        row.refresh_from_db()
        self.assertEqual(row.data["employees"], "1200")

    def test_occupied_cell_untouched(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"name": "Machine Name"},
            column_keys=("name",),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result.occupied, ("name",))
        self.assertEqual(result.written, ())
        row.refresh_from_db()
        self.assertEqual(row.data["name"], "Acme")  # the user's value survives

    def test_clamp_at_cell_max_length(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": ""}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"name": "x" * (CELL_MAX_LENGTH + 8)},
            column_keys=("name",),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result.written, ("name",))
        row.refresh_from_db()
        self.assertEqual(len(row.data["name"]), CELL_MAX_LENGTH)

    def test_type_mismatch_writes_nothing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"employees": "around fifty"},
            column_keys=("employees",),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result.written, ())
        self.assertEqual([m.key for m in result.mismatched], ["employees"])
        self.assertIn("around fifty", result.mismatched[0].why)
        row.refresh_from_db()
        self.assertNotIn("employees", row.data)

    def test_blank_values_skip_entirely(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        before = dict(ListRow.objects.get(id=row.id).data)
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"employees": "", "revenue": "   "},
            column_keys=("employees", "revenue"),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result, ((), (), (), ("employees", "revenue")))
        row.refresh_from_db()
        self.assertEqual(row.data, before)

    def test_the_four_buckets_partition_the_columns_asked_for(self):
        # Every column the write is responsible for lands in exactly one
        # bucket, whether a value arrived for it or not, so the truth
        # rule maps and never defaults. FAILS if a column goes missing
        # from the result (an unanswered one most easily) or lands twice.
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"name": "Machine Name", "employees": "42", "founded": "next spring", "revenue": ""},
            column_keys=("name", "employees", "founded", "revenue", "domain"),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        buckets = [*result.written, *result.occupied, *(m.key for m in result.mismatched), *result.unanswered]
        self.assertEqual(sorted(buckets), sorted(("name", "employees", "founded", "revenue", "domain")))
        self.assertEqual(result.unanswered, ("revenue", "domain"))

    def test_mixed_dict_partial_outcomes(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"name": "Machine Name", "employees": "42", "founded": "next spring", "domain": ""},
            column_keys=("name", "employees", "founded", "domain"),
            truth=CellTruth(source=CellSource.MANUAL),
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
        result = service.write_cells(
            str(target.id),
            str(row.id),
            {"note": "Series B, 2024"},
            column_keys=("note",),
            truth=CellTruth(source=CellSource.MANUAL),
        )
        self.assertEqual(result.written, ("note",))
        row.refresh_from_db()
        self.assertEqual(row.data["note"], "Series B, 2024")

    def test_unknown_row_and_list_read_as_missing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        other = service.create(owner_id="01US" + "A" * 22, label="Other", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        with self.assertRaises(RowNotFound):
            service.write_cells(
                str(target.id),
                "01RW" + "Z" * 22,
                {"name": "x"},
                column_keys=("name",),
                truth=CellTruth(source=CellSource.MANUAL),
            )
        with self.assertRaises(RowNotFound):  # a row outside the list is missing too
            service.write_cells(
                str(other.id),
                str(row.id),
                {"name": "x"},
                column_keys=("name",),
                truth=CellTruth(source=CellSource.MANUAL),
            )
        with self.assertRaises(ListNotFound):
            service.write_cells(
                "01LS" + "Z" * 22,
                str(row.id),
                {"name": "x"},
                column_keys=("name",),
                truth=CellTruth(source=CellSource.MANUAL),
            )
        with self.assertRaises(ListNotFound):  # cross-tenant reads as missing
            _service(account="01AC" + "Z" * 22).write_cells(
                str(target.id),
                str(row.id),
                {"name": "x"},
                column_keys=("name",),
                truth=CellTruth(source=CellSource.MANUAL),
            )


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


class NormalizeRowTests(TestCase):
    """normalize_row is THE shared shape funnel every write path uses, so
    it is unit-tested directly like the validators it wraps, not only
    incidentally through its callers."""

    TYPES = {"score": "number", "when": "date", "name": "text"}

    def test_normalizes_typed_values_and_reports_no_mismatch(self):
        normalized, mismatches = normalize_row(self.TYPES, {"score": "1,234", "when": "2024/8/3", "name": "Acme"})
        self.assertEqual(normalized, {"score": "1234", "when": "2024-08-03", "name": "Acme"})
        self.assertEqual(mismatches, [])

    def test_a_refused_value_is_returned_raw_and_recorded(self):
        # The raw value stays in the map (so a caller can tolerate or flag
        # it) AND the mismatch is recorded (so a caller can reject it).
        normalized, mismatches = normalize_row(self.TYPES, {"score": "banana"})
        self.assertEqual(normalized["score"], "banana")
        self.assertEqual([m.key for m in mismatches], ["score"])

    def test_blank_and_unknown_keys_pass_through_without_mismatch(self):
        normalized, mismatches = normalize_row(self.TYPES, {"score": "", "mystery": "x"})
        self.assertEqual(normalized, {"score": "", "mystery": "x"})  # blank kept; untyped key untouched
        self.assertEqual(mismatches, [])

    def test_zero_is_a_value_not_blank(self):
        # "0" strips truthy, so it is a provided value a number accepts.
        normalized, mismatches = normalize_row(self.TYPES, {"score": "0"})
        self.assertEqual((normalized, mismatches), ({"score": "0"}, []))


class LockGranularityTests(TransactionTestCase):
    """The hazard write_cells guards is a read-modify-write of ONE
    row's json, so the lock is on THAT ROW.

    It used to take `select_for_update` on the LIST, and ListRow was
    never locked anywhere in the codebase. That collapsed the worker's
    64 wide pool to concurrency 1 per sheet at the terminal write, and
    made two unrelated AI columns filling one sheet contend for
    nothing."""

    def test_a_landing_and_a_list_delete_take_the_tables_in_the_same_order(self) -> None:
        # ListRow, then ListCellState, then NodeRun, on both sides: a
        # landing writes the sheet (values and truth as one) and then
        # settles the run; the delete purges rows, truth, then runs. A
        # side that took them in another order is an ABBA deadlock
        # against a fill landing mid-delete. FAILS if either side
        # reorders.
        from lists.constants import NodeRunStatus
        from lists.models import NodeRun
        from lists.nodes.registry import COLUMN_AGENT
        from lists.services.fill_processing.landing import LandingContext, land_row
        from lists.services.node_runs import NodeRunFlow
        from openbower_schema.fills import CellRunResult

        lists = ListService(account_id="01AC" + "A" * 22)
        sheet = lists.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        lists.add_rows(sheet, [{"name": "acme"}])
        row = ListRow.objects.get(list_id=str(sheet.id))
        flow = NodeRunFlow(worker_id="test:order")
        task = NodeRun.objects.create(
            account_id="01AC" + "A" * 22,
            node_id="01ND" + "A" * 22,
            kind=COLUMN_AGENT,
            row_id=str(row.id),
            list_id=str(sheet.id),
            rank=row.rank,
            status=NodeRunStatus.READY,
        )
        assert flow.claim(str(task.id)) is not None
        ctx = LandingContext("01AC" + "A" * 22, str(sheet.id), ("employees",))

        def order(queries, *verbs):
            touched = []
            for q in queries:
                sql = q["sql"].lower()
                for table in ("lists_listrow", "lists_listcellstate", "lists_noderun"):
                    if any(sql.startswith(verb) for verb in verbs) and table in sql.split(" where ")[0]:
                        if table not in touched:
                            touched.append(table)
            return touched

        with CaptureQueriesContext(connection) as captured:
            run = CellRunResult(cells={"employees": "12"})
            land_row(ctx, str(row.id), run, truth=CellTruth.of_agent_run(None, run), flow=flow, task_id=str(task.id))
        self.assertEqual(
            order(captured.captured_queries, "update", "insert"),
            ["lists_listrow", "lists_listcellstate", "lists_noderun"],
        )
        with CaptureQueriesContext(connection) as captured:
            lists.delete(sheet)
        self.assertEqual(
            order(captured.captured_queries, "delete"), ["lists_listrow", "lists_listcellstate", "lists_noderun"]
        )

    def test_the_write_locks_the_row_and_not_the_list(self) -> None:
        lists = ListService(account_id="01AC" + "A" * 22)
        sheet = lists.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        lists.add_rows(sheet, [{"name": "acme"}])
        row = ListRow.objects.get(list_id=str(sheet.id))
        with CaptureQueriesContext(connection) as captured:
            lists.write_cells(
                str(sheet.id),
                str(row.id),
                {"employees": "12"},
                column_keys=("employees",),
                truth=CellTruth(source=CellSource.MANUAL),
            )
        locked = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        self.assertTrue(locked, "the write took no row lock at all")
        for sql in locked:
            self.assertIn("lists_listrow", sql.lower())
            self.assertNotIn('lists_list"', sql.lower())

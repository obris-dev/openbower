"""The one cell writer: write-if-blank under the ROW lock, the clamp,
the per-type shape validators, and machine blanks writing nothing.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_write_cells
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from lists.cells.writes import AnsweredWrite, CellWrite, LandingContext, RowLanding, TypedWrite
from lists.constants import CELL_MAX_LENGTH, CellSource, ColumnType, ListOrigin, StoredCellState
from lists.models import ListRow
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


def _ctx(list_id: str, source=CellSource.MANUAL, fill_run_id: str | None = None) -> LandingContext:
    return LandingContext(list_id=list_id, source=source, fill_run_id=fill_run_id)


def _landing(row_id: str, values: dict[str, str], *, column_keys=None, blank_state=None) -> RowLanding:
    """A landing of value writes for the values (the editor's shape, no
    blank story), plus, when `column_keys` names more, a write with no
    value carrying `blank_state` for each of the rest (an agent's)."""
    # The editor's shape: a blank is no write at all (the kind's rule).
    writes: list[CellWrite] = [TypedWrite(key, value) for key, value in values.items() if value.strip()]
    for key in column_keys or ():
        if key not in values and blank_state is not None:
            writes.append(AnsweredWrite(key, None, blank_state))
    return RowLanding(row_id, writes)


def _sheet(service: ListService, rows: list[dict[str, str]]):
    target = service.create(owner_id="01US" + "A" * 22, label="Sheet", columns=_COLUMNS, origin=ListOrigin.CSV)
    service.add_rows(target, rows)
    return target, service.rows_page(target, limit=len(rows))


class WriteIfBlankTests(TestCase):
    def test_blank_cell_written(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"employees": "1,200"}, column_keys=("employees",), blank_state=None),
        )
        self.assertEqual(result.written, ("employees",))
        self.assertEqual(result.occupied, ())
        self.assertEqual(result.mismatched, ())
        row.refresh_from_db()
        self.assertEqual(row.data["employees"], "1200")

    def test_occupied_cell_untouched(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"name": "Machine Name"}, column_keys=("name",), blank_state=None),
        )
        self.assertEqual(result.occupied, ("name",))
        self.assertEqual(result.written, ())
        row.refresh_from_db()
        self.assertEqual(row.data["name"], "Acme")  # the user's value survives

    def test_clamp_at_cell_max_length(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": ""}])
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"name": "x" * (CELL_MAX_LENGTH + 8)}, column_keys=("name",), blank_state=None),
        )
        self.assertEqual(result.written, ("name",))
        row.refresh_from_db()
        self.assertEqual(len(row.data["name"]), CELL_MAX_LENGTH)

    def test_type_mismatch_writes_nothing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"employees": "around fifty"}, column_keys=("employees",), blank_state=None),
        )
        self.assertEqual(result.written, ())
        self.assertEqual([m.key for m in result.mismatched], ["employees"])
        self.assertIn("around fifty", result.mismatched[0].why)
        row.refresh_from_db()
        self.assertNotIn("employees", row.data)

    def test_a_persons_blank_is_no_write_at_all(self):
        # A blank never reaches the landing as a typed value: the column
        # kind returns None for it, and a TypedWrite refuses to be built
        # from one, so nothing is written and nothing is recorded. FAILS
        # if either door lets a blank through.
        from lists.cells.kinds.registry import column_kind_for
        from lists.models import ListCellState
        from openbower_schema.lists import PlainColumn

        column = PlainColumn(key="employees", label="Employees", type="number")
        self.assertIsNone(column_kind_for(column).on_value_typed(column, "   "))
        with self.assertRaises(ValueError):
            TypedWrite("employees", "")
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        before = dict(ListRow.objects.get(id=row.id).data)
        self.assertEqual(service.land_row(_ctx(str(target.id)), RowLanding(str(row.id), [])), ((), (), ()))
        row.refresh_from_db()
        self.assertEqual(row.data, before)
        self.assertFalse(ListCellState.objects.filter(row_id=str(row.id)).exists())

    def test_every_write_resolves_to_the_state_the_row_earned_it(self):
        # A value that landed is FILLED, one the type refused is
        # TYPE_MISMATCH, one an agent had none for records its cause:
        # each write answers for itself against the row, and every
        # write records exactly one state. A person's blank never
        # becomes a write (the kind returns None; the write refuses to
        # be built). FAILS if the landing derives a state the write did
        # not earn, or a blank slips in as a typed value.
        from lists.models import ListCellState

        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        landing = RowLanding(
            str(row.id),
            [
                TypedWrite("name", "Machine Name"),  # occupied: a person's value stays
                TypedWrite("employees", "42"),  # lands
                TypedWrite("founded", "next spring"),  # the date type refuses
                AnsweredWrite("domain", None, StoredCellState.NO_EVIDENCE),  # an agent had none
            ],
        )
        service.land_row(_ctx(str(target.id), CellSource.NODE), landing)
        recorded = dict(ListCellState.objects.filter(row_id=str(row.id)).values_list("column_key", "state"))
        self.assertEqual(
            recorded,
            {
                "name": StoredCellState.FILLED,
                "employees": StoredCellState.FILLED,
                "founded": StoredCellState.TYPE_MISMATCH,
                "domain": StoredCellState.NO_EVIDENCE,
            },
        )
        with self.assertRaises(ValueError):
            TypedWrite("revenue", "   ")

    def test_a_persons_unanswered_column_leaves_no_record_and_an_agents_carries_its_cause(self):
        # A person has no cause to record: the columns they left blank
        # stay absent from the ledger (never attempted). An agent's run
        # names why it left a column blank, and that column gets the
        # record. FAILS if a person's blank is recorded under a
        # borrowed cause, or an agent's blank goes unrecorded.
        from lists.constants import StoredCellState
        from lists.models import ListCellState

        service = _service()
        target, (row,) = _sheet(service, [{"name": ""}])
        service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"name": "typed"}, column_keys=("name", "employees"), blank_state=None),
        )
        recorded = dict(ListCellState.objects.filter(row_id=str(row.id)).values_list("column_key", "state"))
        self.assertEqual(recorded, {"name": StoredCellState.FILLED})
        service.land_row(
            _ctx(str(target.id), CellSource.NODE),
            _landing(str(row.id), {}, column_keys=("employees",), blank_state=StoredCellState.NO_EVIDENCE),
        )
        recorded = dict(ListCellState.objects.filter(row_id=str(row.id)).values_list("column_key", "state"))
        self.assertEqual(recorded, {"name": StoredCellState.FILLED, "employees": StoredCellState.NO_EVIDENCE})

    def test_a_batch_lands_in_three_statements_whatever_its_size(self):
        # Two landings on one row merge, and rows across the batch are
        # locked in ONE statement, written in ONE update, recorded in
        # ONE upsert. FAILS if the landing locks, writes or records per
        # row or per landing.
        service = _service()
        target, (first, second) = _sheet(service, [{"name": ""}, {"name": ""}])
        landings = [
            _landing(str(first.id), {"name": "Acme"}),
            _landing(str(first.id), {"employees": "12"}),
            _landing(str(second.id), {"name": "Example"}),
        ]
        with CaptureQueriesContext(connection) as captured:
            verdicts = service.land_rows(_ctx(str(target.id)), landings)
        self.assertEqual(verdicts[str(first.id)].written, ("name", "employees"))
        self.assertEqual(verdicts[str(second.id)].written, ("name",))
        sql = [q["sql"] for q in captured.captured_queries]
        locks = [q for q in sql if "FOR UPDATE" in q.upper()]
        updates = [q for q in sql if q.upper().startswith("UPDATE") and "lists_listrow" in q]
        upserts = [q for q in sql if "INSERT INTO" in q and "cellstate" in q]
        self.assertEqual((len(locks), len(updates), len(upserts)), (1, 1, 1))
        first.refresh_from_db()
        self.assertEqual((first.data["name"], first.data["employees"]), ("Acme", "12"))

    def test_the_column_kinds_answer_a_typed_value_each_in_their_own_way(self):
        # The cell-shaped change: a plain or AI column lands the value
        # FILLED, a blank is nothing to write, a Send webhook column
        # refuses (its cell is its node's). The roster covers every kind
        # the wire can carry. FAILS if a kind is missing or answers
        # differently.
        from lists.cells.kinds.base import NotEditable
        from lists.cells.kinds.registry import column_kind_for, registered_kinds, wire_column_kinds
        from openbower_schema.lists import AiColumn, PlainColumn, WebhookColumn

        self.assertEqual(set(registered_kinds()), set(wire_column_kinds()))
        plain = PlainColumn(key="name", label="Name", type="text")
        ai = AiColumn(key="answer", label="Answer", type="text", node_id="01ND" + "A" * 22)
        hook = WebhookColumn(key="crm_sync", label="CRM", type="text", node_id="01ND" + "B" * 22)
        self.assertEqual(column_kind_for(plain).on_value_typed(plain, "Acme"), TypedWrite("name", "Acme"))
        self.assertEqual(column_kind_for(ai).on_value_typed(ai, "yes"), TypedWrite("answer", "yes"))
        self.assertIsNone(column_kind_for(ai).on_value_typed(ai, "   "))
        with self.assertRaises(NotEditable):
            column_kind_for(hook).on_value_typed(hook, "sent")

    def test_the_agent_kind_writes_one_cell_per_column_it_fills(self):
        # The run-shaped change: one write per column the node fills,
        # FILLED with the answer or the run's cause without one; a key
        # the run answered that the node does not fill is never written.
        # FAILS if the kind iterates the result's keys instead of its
        # columns.
        from lists.models import Node
        from lists.nodes.registry import COLUMN_AGENT
        from lists.processors import WalkScope
        from lists.processors.column_agent import AIColumnProcessor
        from openbower_schema.fills import CellRunResult

        node = Node(id="01ND" + "A" * 22, account_id="01AC" + "A" * 22, kind=COLUMN_AGENT)
        processor = AIColumnProcessor(account_id="01AC" + "A" * 22, node=node, scope=WalkScope())
        result = CellRunResult(
            cells={"answer": "yes", "stray": "x"},
            declined_cause=StoredCellState.NO_EVIDENCE,
            tools={"web_search": "open"},
        )
        self.assertEqual(
            processor.on_run_landed(("answer", "score"), result),
            [
                AnsweredWrite("answer", "yes", StoredCellState.NO_EVIDENCE, tools={"web_search": "open"}),
                AnsweredWrite("score", None, StoredCellState.NO_EVIDENCE, tools={"web_search": "open"}),
            ],
        )

    def test_mixed_dict_partial_outcomes(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme", "employees": ""}])
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(
                str(row.id),
                {"name": "Machine Name", "employees": "42", "founded": "next spring", "domain": ""},
                column_keys=("name", "employees", "founded", "domain"),
                blank_state=None,
            ),
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
        result = service.land_row(
            _ctx(str(target.id), CellSource.MANUAL),
            _landing(str(row.id), {"note": "Series B, 2024"}, column_keys=("note",), blank_state=None),
        )
        self.assertEqual(result.written, ("note",))
        row.refresh_from_db()
        self.assertEqual(row.data["note"], "Series B, 2024")

    def test_unknown_row_and_list_read_as_missing(self):
        service = _service()
        target, (row,) = _sheet(service, [{"name": "Acme"}])
        other = service.create(owner_id="01US" + "A" * 22, label="Other", columns=_COLUMNS, origin=ListOrigin.MANUAL)
        with self.assertRaises(RowNotFound):
            service.land_row(
                _ctx(str(target.id), CellSource.MANUAL),
                _landing("01RW" + "Z" * 22, {"name": "x"}, column_keys=("name",), blank_state=None),
            )
        with self.assertRaises(RowNotFound):  # a row outside the list is missing too
            service.land_row(
                _ctx(str(other.id), CellSource.MANUAL),
                _landing(str(row.id), {"name": "x"}, column_keys=("name",), blank_state=None),
            )
        with self.assertRaises(ListNotFound):
            service.land_row(
                _ctx("01LS" + "Z" * 22, CellSource.MANUAL),
                _landing(str(row.id), {"name": "x"}, column_keys=("name",), blank_state=None),
            )
        with self.assertRaises(ListNotFound):  # cross-tenant reads as missing
            _service(account="01AC" + "Z" * 22).land_row(
                _ctx(str(target.id), CellSource.MANUAL),
                _landing(str(row.id), {"name": "x"}, column_keys=("name",), blank_state=None),
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
        from django.db import transaction

        from lists.constants import NodeRunStatus
        from lists.models import NodeRun
        from lists.nodes.registry import COLUMN_AGENT
        from lists.services.node_runs import NodeRunFlow

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
        ctx = _ctx(str(sheet.id), CellSource.NODE)

        def order(queries, *verbs):
            touched = []
            for q in queries:
                sql = q["sql"].lower()
                for table in ("lists_listrow", "lists_listcellstate", "lists_noderun"):
                    if any(sql.startswith(verb) for verb in verbs) and table in sql.split(" where ")[0]:
                        if table not in touched:
                            touched.append(table)
            return touched

        with CaptureQueriesContext(connection) as captured, transaction.atomic():
            # The processor's landing: the writes, then the close.
            lists.land_row(ctx, _landing(str(row.id), {"employees": "12"}))
            assert flow.settle(str(task.id), result={}, status=NodeRunStatus.DONE)
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
            lists.land_row(
                _ctx(str(sheet.id), CellSource.MANUAL),
                _landing(str(row.id), {"employees": "12"}, column_keys=("employees",), blank_state=None),
            )
        locked = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        self.assertTrue(locked, "the write took no row lock at all")
        for sql in locked:
            self.assertIn("lists_listrow", sql.lower())
            self.assertNotIn('lists_list"', sql.lower())

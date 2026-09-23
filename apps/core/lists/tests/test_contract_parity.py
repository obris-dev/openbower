"""The wire contract's Literals and the Django enums must name the
same values, or valid rows fail zod client-side with no server error.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from jobs.constants import JobStatus
from lists import constants
from lists.services.fill_progress import word_of
from openbower_schema.fills import FillStatusWire
from openbower_schema.lists import ColumnType as WireColumnType
from openbower_schema.lists import ListOrigin as WireListOrigin
from openbower_schema.lists import WireCellState
from openbower_schema.runs import OPEN_NODE_RUN_STATES, NodeRunStatusWire


class WireEnumParityTests(SimpleTestCase):
    def test_column_type_parity(self):
        self.assertEqual(set(get_args(WireColumnType)), {v.value for v in constants.ColumnType})

    def test_list_origin_parity(self):
        self.assertEqual(set(get_args(WireListOrigin)), {v.value for v in constants.ListOrigin})

    def test_cell_state_parity(self):
        # The wire adds `pending` (derived, never stored) and otherwise
        # names exactly the stored members: a digest's `states` map is
        # typed over the wire vocabulary, so a new stored state must
        # reach it.
        self.assertEqual(set(get_args(WireCellState)) - {"pending"}, {v.value for v in constants.StoredCellState})

    def test_node_run_status_parity(self):
        # The contract's status literal and its OPEN partition are the
        # server's NodeRunStatus and NON_TERMINAL set, member for member:
        # the preview poll derives its loop predicate off the wire, so a
        # status added on one side only would either never terminate a
        # poll or end one early.
        self.assertEqual(set(get_args(NodeRunStatusWire)), {str(status) for status in constants.NodeRunStatus})
        self.assertEqual(set(OPEN_NODE_RUN_STATES), {str(status) for status in constants.NON_TERMINAL_NODE_RUN_STATES})

    def test_fill_status_parity(self):
        # Every job status has a word, and every word is reachable: the
        # derivation is exhaustive, so a status added to the job's
        # lifecycle fails here instead of reading as pending forever.
        words = set(get_args(FillStatusWire))
        derived = {word_of(status, started=started) for status in JobStatus for started in (False, True)}
        self.assertEqual(derived, words)
        with self.assertRaises(ValueError):
            word_of("paused", started=False)


class RowDoorPins(SimpleTestCase):
    def test_rows_enter_a_sheet_through_the_one_append(self):
        # ListService.add_rows is the primitive (rows and nothing else);
        # AppendRowsOperation is the door (the primitive, then the
        # workflow trigger, one transaction). In production the door is
        # the primitive's ONLY caller, so no path can add rows the
        # workflow never hears about. Tests seed rows with the primitive
        # on purpose (a fixture is not a door). FAILS if a new caller
        # reaches for the primitive.
        import re
        from pathlib import Path

        core = Path(__file__).resolve().parents[2]
        callers = []
        for path in core.rglob("*.py"):
            if "/tests/" in str(path) or path.name.startswith("test_"):
                continue
            text = path.read_text()
            if re.search(r"\.add_rows\(", text) and not re.search(r"def add_rows\(", text):
                callers.append(str(path.relative_to(core)))
        self.assertEqual(callers, ["lists/operations/append_rows.py"])

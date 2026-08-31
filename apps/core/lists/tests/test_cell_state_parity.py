"""The two cell-state vocabularies, and the exact relation between
them.

There are two on purpose. StoredCellState is what a fill WROTE about a
cell; WireCellState is what a client renders. They differ in both
directions and one serializer projection stands between them:

  filled  is stored, and travels ONLY when the run that filled the
          cell had a degraded tool (the value is the renderer's
          already; the mark beside it is not). A value plus no entry
          IS the clean filled signal.
  pending is on the wire and never stored. It is DERIVED from queued
          tasks on live fills, which is what lets admission write
          nothing to the sheet and a stopped fill need no sweep.

Everything else must appear in both, or a cause the server can write
is a cause the client cannot name. Nothing enforced that until this
file: the two were both called CellState, so a call site could not
tell them apart.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from openbower_schema.fills import SETTLED_CELL_STATES
from openbower_schema.lists import WireCellState

from ..constants import CELL_STATE_MAX_LENGTH, StoredCellState

# `filled` travels only for a cell whose run had a degraded tool (the
# mark beside the value); a clean filled cell is still an absence.
STORED_ONLY: set[str] = set()
WIRE_ONLY = {"pending"}


class CellStateParityTests(SimpleTestCase):
    def test_the_vocabularies_differ_by_exactly_the_two_documented_words(self):
        stored = {member.value for member in StoredCellState}
        wire = set(get_args(WireCellState))
        self.assertEqual(stored - wire, STORED_ONLY)
        self.assertEqual(wire - stored, WIRE_ONLY)

    def test_every_settled_cause_is_a_real_state_in_both(self):
        # ONE definition of the partition, in the contract, read by the
        # server directly, so the two sides cannot hold different
        # lists. What they CAN do is drift from the vocabularies: a
        # cause typed here that no state carries would silently make
        # that state re-runnable. It is typed WireCellState so a typo
        # fails type check; this pins the STORAGE side, which is what
        # the refill targeting query compares against.
        stored = {member.value for member in StoredCellState}
        wire = set(get_args(WireCellState))
        for cause in SETTLED_CELL_STATES:
            with self.subTest(cause=cause):
                self.assertIn(cause, stored)
                self.assertIn(cause, wire)

    def test_neither_exclusive_word_leaks_into_the_settled_partition(self):
        # filled is not a blank cause; pending is not terminal.
        self.assertFalse(set(SETTLED_CELL_STATES) & (STORED_ONLY | WIRE_ONLY))

    def test_every_base_status_has_a_cell_state_and_every_retry_cause_re_runs(self):
        # A closed door lands as the cell state its code maps to, for
        # EVERY tool's vocabulary; a code added without a row would
        # KeyError mid-run. Not configured is written at once (nothing
        # to retry); every other closed door parks. And a retry cause
        # that was also settled would park a row and then never re-run
        # it.
        from agents.constants import SearchStatus, ToolStatus

        from ..constants import CELL_STATE_BY_STATUS, RETRY_CAUSES

        self.assertEqual(set(CELL_STATE_BY_STATUS), {s.value for s in ToolStatus} - {ToolStatus.OPEN})
        for status in SearchStatus:
            if status is not SearchStatus.OPEN:
                self.assertIn(status, CELL_STATE_BY_STATUS)
        self.assertEqual(CELL_STATE_BY_STATUS[ToolStatus.NOT_CONFIGURED], StoredCellState.TOOL_NOT_CONFIGURED)
        for status, state in CELL_STATE_BY_STATUS.items():
            if status is not ToolStatus.NOT_CONFIGURED:
                self.assertIn(state, RETRY_CAUSES)
        self.assertNotIn(StoredCellState.TOOL_NOT_CONFIGURED, RETRY_CAUSES)
        self.assertFalse(set(RETRY_CAUSES) & set(SETTLED_CELL_STATES))
        self.assertNotIn(StoredCellState.TOOL_NOT_CONFIGURED, SETTLED_CELL_STATES)
        for state in StoredCellState:
            self.assertLessEqual(len(state.value), CELL_STATE_MAX_LENGTH)

    def test_a_search_stored_before_the_status_fields_still_reads(self):
        # TestSearch's defaults exist FOR the stored read: task
        # results and bench runs written before the door-status fields
        # carry only query/hits (plus retired keys). A required field
        # there bricks the drawer on every old row; this fails if one
        # of the four fields loses its default.
        from openbower_schema.agents import TestSearch

        search = TestSearch.model_validate({"query": "acme", "hits": 3, "failed": True, "cause": "timeout"})
        self.assertEqual((search.status, search.provider, search.attempts, search.tool), ("", "", 1, ""))

    def test_every_tools_status_vocabulary_contains_the_base_and_ships(self):
        # StrEnums cannot extend one another, so each tool's enum
        # restates the base; this is what makes that a rule. The
        # REGISTRY is the add-a-tool pin: a new AgentTool member with
        # no spec fails here, before a missing function, label, door,
        # or cell-state row could surface as a KeyError mid-run. And
        # the wire's per-tool lists (the client's copy-table types)
        # are the registry's own enums, so a code added server-side
        # reaches the client.
        from agents.constants import AgentTool, ToolStatus
        from agents.runtime.tools import TOOL_REGISTRY
        from openbower_schema.agents import TOOL_STATUSES, ToolStatusWire

        from ..constants import CELL_STATE_BY_STATUS

        self.assertEqual(set(TOOL_REGISTRY), set(AgentTool))
        self.assertEqual(set(get_args(ToolStatusWire)), {s.value for s in ToolStatus})
        self.assertEqual(set(TOOL_STATUSES), {t.value for t in AgentTool})
        for tool, spec in TOOL_REGISTRY.items():
            with self.subTest(tool=tool.value):
                self.assertLessEqual({s.value for s in ToolStatus}, {s.value for s in spec.statuses})
                self.assertEqual(set(TOOL_STATUSES[tool.value]), {s.value for s in spec.statuses})
                for status in spec.statuses:
                    if status.value != ToolStatus.OPEN:
                        self.assertIn(status.value, CELL_STATE_BY_STATUS)

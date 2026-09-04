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
        # filled is not a blank cause; pending is not terminal. FILLED
        # is asserted directly because it left STORED_ONLY when it
        # started traveling, which made the set-intersection half of
        # this vacuous for it.
        self.assertFalse(set(SETTLED_CELL_STATES) & (STORED_ONLY | WIRE_ONLY))
        self.assertNotIn(StoredCellState.FILLED, SETTLED_CELL_STATES)
        self.assertNotIn("pending", SETTLED_CELL_STATES)

    def test_every_failure_mode_lands_as_a_state_and_every_retry_cause_re_runs(self):
        # A tool failure lands as the state the harness maps its MODE
        # to. FATAL (not configured) is written at once (nothing to
        # retry); hazard and transient park. And a retry cause that
        # was also settled would park a row and then never re-run it.
        from agents.tools import registry as tool_registry
        from agents.tools.base import FailureMode
        from lists.services.cell_run import _TOOL_FAILURE_MODE_STATES

        from ..constants import RETRY_CAUSES

        self.assertEqual(set(_TOOL_FAILURE_MODE_STATES), set(FailureMode))
        for tool in tool_registry.all_tools():
            for code, mode in tool.failure_modes.items():
                with self.subTest(tool=tool.name, code=code):
                    state = _TOOL_FAILURE_MODE_STATES[mode]
                    if mode is FailureMode.FATAL:
                        self.assertEqual(state, StoredCellState.TOOL_NOT_CONFIGURED)
                        self.assertNotIn(state, RETRY_CAUSES)
                    else:
                        self.assertIn(state, RETRY_CAUSES)
        self.assertNotIn(StoredCellState.TOOL_NOT_CONFIGURED, RETRY_CAUSES)
        self.assertFalse(set(RETRY_CAUSES) & set(SETTLED_CELL_STATES))
        self.assertNotIn(StoredCellState.TOOL_NOT_CONFIGURED, SETTLED_CELL_STATES)
        for state in StoredCellState:
            self.assertLessEqual(len(state.value), CELL_STATE_MAX_LENGTH)

    def test_every_tools_failure_vocabulary_ships_and_carries_copy(self):
        # The failure_modes KEYS are each tool's declared failure
        # vocabulary; the wire's per-tool lists (the client's
        # copy-table types) must carry exactly those codes plus the
        # one reserved "open", so a code added server-side reaches the
        # client. And every code must produce tier-1 failure copy: the
        # breaker quotes tool.failure_copy(code) verbatim into a
        # failed fill's message.
        from agents.constants import ToolStatus
        from agents.tools import registry as tool_registry
        from openbower_schema.agents import TOOL_STATUSES, AgentTools, ToolStatusWire

        self.assertEqual({tool.name for tool in tool_registry.all_tools()}, set(AgentTools.model_fields))
        self.assertEqual(set(get_args(ToolStatusWire)), {s.value for s in ToolStatus})
        self.assertEqual(set(TOOL_STATUSES), set(AgentTools.model_fields))
        for tool in tool_registry.all_tools():
            with self.subTest(tool=tool.name):
                self.assertEqual(set(TOOL_STATUSES[tool.name]), set(tool.failure_modes) | {ToolStatus.OPEN.value})
                for code in tool.failure_modes:
                    copy = tool.failure_copy(code)
                    self.assertTrue(copy.problem)

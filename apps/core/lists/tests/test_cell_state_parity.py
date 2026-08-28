"""The two cell-state vocabularies, and the exact relation between
them.

There are two on purpose. StoredCellState is what a fill WROTE about a
cell; WireCellState is what a client renders. They differ in both
directions and one serializer projection stands between them:

  filled  is stored and never travels. A value on the row plus no
          state IS the filled signal, so shipping the word would be
          something the renderer already knows.
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

STORED_ONLY = {"filled"}
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

    def test_every_tool_has_a_throttled_state_and_every_retry_cause_re_runs(self):
        # A closed door lands as the state its TOOL owns; a tool added
        # without one would KeyError mid-run. And a retry cause that
        # was also settled would park a row and then never re-run it.
        from agents.constants import AgentTool

        from ..constants import RETRY_CAUSES, THROTTLED_STATE_BY_TOOL

        self.assertEqual(set(THROTTLED_STATE_BY_TOOL), set(AgentTool))
        for state in THROTTLED_STATE_BY_TOOL.values():
            self.assertIn(state, RETRY_CAUSES)
        self.assertFalse(set(RETRY_CAUSES) & set(SETTLED_CELL_STATES))
        for state in StoredCellState:
            self.assertLessEqual(len(state.value), CELL_STATE_MAX_LENGTH)

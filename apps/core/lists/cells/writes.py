"""What a cell wants to become, and what a landing is made of.

Two shapes, told apart by what they carry, never by inspecting a
field: a VALUE write (a person's or an agent's: land this where the
cell is blank, and record what the row made of that) and a STATE
write (a column that holds no value, a Send webhook's: the state IS
the write). Each resolves ITSELF against what the row reported, so
the landing carries writes and never interprets them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from ..constants import CellSource, StoredCellState


class CellMismatch(NamedTuple):
    """One refused key and the why a user can act on."""

    key: str
    why: str


class RowVerdict(NamedTuple):
    """What the value pass made of one row: the keys whose value landed
    (written), whose cell already held one that write-if-blank kept
    (occupied), and whose value the column's type refused (mismatched).
    A key in none of these landed nothing."""

    written: tuple[str, ...]
    occupied: tuple[str, ...]
    mismatched: tuple[CellMismatch, ...]

    @property
    def filled(self) -> frozenset[str]:
        return frozenset((*self.written, *self.occupied))

    @property
    def refused(self) -> frozenset[str]:
        return frozenset(mismatch.key for mismatch in self.mismatched)


class StateWrite(NamedTuple):
    """A cell's state, as the thing to record: what every write resolves
    to once the row has spoken, and what a column that holds no value
    (a Send webhook's) writes directly: SENT, FAILED."""

    key: str
    state: StoredCellState
    tools: Mapping[str, str] = {}

    def value_to_land(self) -> str | None:
        return None

    def resolve(self, verdict: RowVerdict) -> StateWrite:
        return self


class ValueWrite(NamedTuple):
    """A value for a column that holds one: a person's (a non-blank
    value, no blank story: `blank_state` None) or an agent's (the answer
    it had, or None, and the cause to record when nothing lands). It
    resolves to FILLED when its value landed or the cell was occupied,
    TYPE_MISMATCH when the column's type refused it, else its
    blank_state, or to nothing at all when it has none (a person's
    blank records no cell)."""

    key: str
    value: str | None
    blank_state: StoredCellState | None = None
    tools: Mapping[str, str] = {}

    def value_to_land(self) -> str | None:
        return self.value if self.value and self.value.strip() else None

    def resolve(self, verdict: RowVerdict) -> StateWrite | None:
        if self.key in verdict.filled:
            return StateWrite(self.key, StoredCellState.FILLED, self.tools)
        if self.key in verdict.refused:
            return StateWrite(self.key, StoredCellState.TYPE_MISMATCH, self.tools)
        if self.blank_state is None:
            return None
        return StateWrite(self.key, self.blank_state, self.tools)


CellWrite = ValueWrite | StateWrite


class RowLanding(NamedTuple):
    """One row's writes from one change: N columns of one node's run,
    or the one cell a person typed. Several landings on one row in a
    batch are merged by the landing under one row lock."""

    row_id: str
    writes: Sequence[CellWrite]


class Landed(NamedTuple):
    """What the value pass handed back for one row: the row's verdict,
    and every write resolved to the state it records (a person's blank
    resolves to nothing and is absent)."""

    verdict: RowVerdict
    states: tuple[StateWrite, ...]


class LandingContext(NamedTuple):
    """The facts every record of a landing carries, the same for every
    row it lands: the sheet, who is writing, and the fill the records
    belong to (None off a fill)."""

    list_id: str
    source: CellSource
    fill_run_id: str | None

"""What a cell wants to become, and what a landing is made of.

One write per OPERATION on a cell (a person typed, an agent answered,
a webhook sent), all one base: what the operation has to land, and
the mapping from what the row made of that to the state it records.
Each operation writes its own mapping in full. Every write resolves to
exactly one StateWrite, the one shape the ledger takes, so the landing
carries writes and never interprets them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    """The terminal shape: a cell's state as the thing to record. Every
    write resolves to one (or to nothing) once the row has spoken, and
    the ledger upsert takes these and only these."""

    key: str
    state: StoredCellState
    tools: Mapping[str, str] = {}


@dataclass(frozen=True)
class CellWrite(ABC):
    """One operation's intent on one cell, BEFORE the row has spoken:
    what it has to land, and how what the row made of that becomes the
    state it records. Each operation writes its own mapping in full,
    so the whole of it is read in one place. In memory only: a kind
    builds these, the landing persists what they resolve to."""

    key: str
    tools: Mapping[str, str] = field(default_factory=dict, kw_only=True)

    def value_to_land(self) -> str | None:
        """The value to write where the cell is blank, or None."""
        return None

    @abstractmethod
    def resolve(self, verdict: RowVerdict) -> StateWrite:
        """The state this write records, given what the row reported.
        Total: every write records exactly one state."""


@dataclass(frozen=True)
class TypedWrite(CellWrite):
    """A person set a cell. The value is never blank (a blank is nothing
    to write and nothing to record, decided by the column kind before a
    write exists; here it is refused), so it lands or the type refuses
    it."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError(f"a typed value for {self.key!r} cannot be blank")

    def value_to_land(self) -> str | None:
        return self.value

    def resolve(self, verdict: RowVerdict) -> StateWrite:
        if self.key in verdict.refused:
            return StateWrite(self.key, StoredCellState.TYPE_MISMATCH, self.tools)
        return StateWrite(self.key, StoredCellState.FILLED, self.tools)


@dataclass(frozen=True)
class AnsweredWrite(CellWrite):
    """An agent's run on a column it fills: the answer it had (or none)
    and the cause it records when nothing lands, with the run's tool
    statuses on whichever state it resolves to."""

    value: str | None
    cause: StoredCellState

    def value_to_land(self) -> str | None:
        return self.value if self.value and self.value.strip() else None

    def resolve(self, verdict: RowVerdict) -> StateWrite:
        if self.key in verdict.filled:
            return StateWrite(self.key, StoredCellState.FILLED, self.tools)
        if self.key in verdict.refused:
            return StateWrite(self.key, StoredCellState.TYPE_MISMATCH, self.tools)
        return StateWrite(self.key, self.cause, self.tools)


@dataclass(frozen=True)
class WebhookWrite(CellWrite):
    """A Send webhook's outcome on its column, which holds no value:
    nothing to land, so the row has nothing to say, and the state
    (SENT, FAILED) is what it records."""

    state: StoredCellState

    def resolve(self, verdict: RowVerdict) -> StateWrite:
        return StateWrite(self.key, self.state, self.tools)


class RowLanding(NamedTuple):
    """One row's writes from one change: N columns of one node's run,
    or the one cell a person typed. Several landings on one row in a
    batch are merged by the landing under one row lock."""

    row_id: str
    writes: Sequence[CellWrite]


class Landed(NamedTuple):
    """What the value pass handed back for one row: the row's verdict,
    and every write resolved to the state it records."""

    verdict: RowVerdict
    states: tuple[StateWrite, ...]


class LandingContext(NamedTuple):
    """The facts every record of a landing carries, the same for every
    row it lands: the sheet, who is writing, and the fill the records
    belong to (None off a fill)."""

    list_id: str
    source: CellSource
    fill_run_id: str | None

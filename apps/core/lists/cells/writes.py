"""What a cell wants to become, and what a landing is made of."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from ..constants import CellSource, StoredCellState


class CellWrite(NamedTuple):
    """One cell's intent on a row: the state it means to record, the
    value it lands where the cell is blank (None for a column that
    holds none, or for a blank an agent explains), and the tool
    statuses behind it. In memory only: a kind builds these, the
    landing persists them. The landing has the last word on two
    things the writer cannot know: a value the column's type refuses
    records TYPE_MISMATCH, and a value that finds the cell occupied
    records FILLED (the cell IS filled, not by this write)."""

    key: str
    state: StoredCellState
    value: str | None = None
    tools: Mapping[str, str] = {}


class RowLanding(NamedTuple):
    """One row's writes from one change: N columns of one node's run,
    or the one cell a person typed. Several landings on one row in a
    batch are merged by the landing under one row lock."""

    row_id: str
    writes: Sequence[CellWrite]


class LandingContext(NamedTuple):
    """The facts every record of a landing carries, the same for every
    row it lands: the sheet, who is writing, and the fill the records
    belong to (None off a fill)."""

    list_id: str
    source: CellSource
    fill_run_id: str | None

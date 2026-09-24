"""What a column kind IS: how one cell of that kind reacts to a change
a person makes to it, as a CellWrite or a refusal. Pure: a kind
persists nothing and knows no run."""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from openbower_schema.lists import ListColumn

from ..writes import CellWrite


class NotEditable(Exception):
    """The column holds no value a person can set (a Send webhook's
    cell is its node's to write)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"column {key!r} cannot be edited")
        self.key = key


class ColumnKind(ABC):
    KIND: ClassVar[str]
    # Whether this kind's results are recorded as ListCellState, which
    # is how automation knows what a cell holds and when it was last
    # touched. The value and that record are written TOGETHER by the
    # landing, so a writer of rows alone cannot carry one. Declared by
    # every kind, never defaulted: the two defaults fail opposite ways,
    # and the silent one (a recorded kind assumed unrecorded) stores a
    # value with no record, which reads as never attempted forever and
    # completes no barrier.
    RECORDS_CELL_STATE: ClassVar[bool]

    def on_value_typed(self, column: ListColumn, value: str) -> CellWrite | None:
        """A person set this cell. The TypedWrite to land (a blank is
        nothing to write and nothing to record, so the kind returns
        None for one), or NotEditable for a kind that holds no value.
        The default is the refusal: a kind that accepts typing says
        so."""
        raise NotEditable(column.key)

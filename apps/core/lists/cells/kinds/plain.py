"""A plain column: a person's value, nothing else ever writes it."""

from __future__ import annotations

from typing import ClassVar

from openbower_schema.lists import ListColumn

from ..writes import CellWrite, TypedWrite
from .base import ColumnKind
from .registry import register


class PlainColumnKind(ColumnKind):
    KIND: ClassVar[str] = "plain"
    # Nothing records what a person meant by a value they typed.
    RECORDS_CELL_STATE: ClassVar[bool] = False

    def on_value_typed(self, column: ListColumn, value: str) -> CellWrite | None:
        if not value.strip():
            return None
        return TypedWrite(column.key, value)


register(PlainColumnKind)

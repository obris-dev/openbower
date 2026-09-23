"""A plain column: a person's value, nothing else ever writes it."""

from __future__ import annotations

from typing import ClassVar

from openbower_schema.lists import ListColumn

from ...constants import StoredCellState
from ..writes import CellWrite
from .base import ColumnKind
from .registry import register


class PlainColumnKind(ColumnKind):
    KIND: ClassVar[str] = "plain"

    def on_value_typed(self, column: ListColumn, value: str) -> CellWrite | None:
        if not value.strip():
            return None
        return CellWrite(column.key, state=StoredCellState.FILLED, value=value)


register(PlainColumnKind)

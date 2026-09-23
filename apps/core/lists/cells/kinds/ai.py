"""An AI column: an agent's node fills it (the node kind's landing);
a person may fill it too, and a typed value is FILLED like any
other, write-if-blank protecting it from the next run."""

from __future__ import annotations

from typing import ClassVar

from openbower_schema.lists import ListColumn

from ...constants import StoredCellState
from ..writes import CellWrite
from .base import ColumnKind
from .registry import register


class AiColumnKind(ColumnKind):
    KIND: ClassVar[str] = "ai"

    def on_value_typed(self, column: ListColumn, value: str) -> CellWrite | None:
        if not value.strip():
            return None
        return CellWrite(column.key, state=StoredCellState.FILLED, value=value)


register(AiColumnKind)

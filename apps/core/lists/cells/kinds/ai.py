"""An AI column: an agent's node fills it (the node kind's landing);
a person may fill it too, and a typed value is FILLED like any
other, write-if-blank protecting it from the next run."""

from __future__ import annotations

from typing import ClassVar

from openbower_schema.lists import ListColumn

from ..writes import CellWrite, TypedWrite
from .base import ColumnKind
from .registry import register


class AiColumnKind(ColumnKind):
    KIND: ClassVar[str] = "ai"
    # What the run made of the cell: the value or the cause it declined
    # with, and the tools behind either.
    RECORDS_CELL_STATE: ClassVar[bool] = True

    def on_value_typed(self, column: ListColumn, value: str) -> CellWrite | None:
        if not value.strip():
            return None
        return TypedWrite(column.key, value)


register(AiColumnKind)

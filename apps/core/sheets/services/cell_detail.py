"""What one cell's record says about it, for the person who opens the
cell: its state, and when an agent settled it, how sure the model was
and why. Read off the record alone, never off the run that wrote it,
so the run stays the fill's audit."""

from __future__ import annotations

from agents.confidence import confidence_level
from openbower_schema.sheets import CellDetailWire

from ..models import Sheet
from .cell_states import CellStateService
from .columns import ColumnNotFound
from .sheets import SheetService


class CellDetailService:
    def __init__(self, *, account_id: str) -> None:
        self.sheets = SheetService(account_id=account_id)
        self.cell_states = CellStateService(account_id=account_id)

    def detail(self, target_sheet: Sheet, *, row_id: str, key: str) -> CellDetailWire:
        """Raises ColumnNotFound and RowNotFound; a cell no writer has
        reached answers with every field empty."""
        if not any(column.key == key for column in target_sheet.columns):
            raise ColumnNotFound(key)
        self.sheets.row(target_sheet, row_id)
        record = self.cell_states.record_of(str(target_sheet.id), row_id=row_id, column_key=key)
        if record is None:
            return CellDetailWire()
        if record.detail is None:
            return CellDetailWire(state=record.state)
        return CellDetailWire(
            state=record.state,
            level=confidence_level(record.detail.confidence),
            reason=record.detail.reason,
        )

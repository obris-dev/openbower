"""A person setting cells on one row: the values land through the cell
writer as the person's own writes (they replace what is there), and
the workflow is told, in ONE transaction, so a hand-filled cell on an
AI column completes a barrier the same way a run's answer would and no
door can forget the second half. Told to COMPLETE, never to advance:
an edit is one cell of a row that may still be being edited, so the
node behind the edited column is not run off it; the person asks for
that run.

Refusals come back as CellRefused, never as a flag on the cell: a
machine's mismatched answer is recorded and the row moves on, but a
person is handed the reason and keeps the cell they had."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from openbower_schema.lists import AiColumn, PlainColumn

from ..cells import CellWrite, LandingContext, RowLanding, TypedWrite
from ..constants import CellSource
from ..models import List, ListRow
from ..services.lists import CellBlank, CellColumnUnknown, CellNotWritable, CellTypeRefused, ListService
from ..services.workflow_reactions import WorkflowReactions


@dataclass
class SetCellsReport:
    """What one write did: the row as it now reads, and the runs the
    workflow queued because of it."""

    row: ListRow
    queued: int


class SetCellsOperation:
    def __init__(self, *, account_id: str, target_list: List, row_id: str, cells: dict[str, str]) -> None:
        self.account_id = account_id
        self.target_list = target_list
        self.row_id = row_id
        self.cells = dict(cells)

    def run(self) -> SetCellsReport:
        """Raises CellColumnUnknown for a key the sheet has no column
        for, CellNotWritable for a column that holds no value a person
        can set, CellBlank for a blank value, CellTypeRefused for a value outside the
        column's type, and RowNotFound for a row that is gone. Nothing
        is written when anything is refused."""
        columns = {column.key: column for column in self.target_list.columns}
        for key, value in self.cells.items():
            column = columns.get(key)
            if column is None:
                raise CellColumnUnknown(key)
            # Named kinds, never "all but one": a kind added later takes
            # no value until it says it does.
            if not isinstance(column, PlainColumn | AiColumn):
                raise CellNotWritable(column_label=column.label)
            if not value.strip():
                raise CellBlank(column_label=column.label)
        writes: list[CellWrite] = [TypedWrite(key, value) for key, value in self.cells.items()]
        ctx = LandingContext(list_id=str(self.target_list.id), source=CellSource.MANUAL, fill_run_id=None)
        lists = ListService(account_id=self.account_id)
        with transaction.atomic():
            verdict = lists.land_row(ctx, RowLanding(self.row_id, writes))
            # The ONE type check: the landing's, under the row's lock
            # against the sheet as it is then. It reports a mismatch
            # rather than raising (a machine's answer is flagged and
            # the row moves on); a person's is refused here, and the
            # raise rolls the landing back, so nothing was written.
            if verdict.mismatched:
                first = verdict.mismatched[0]
                raise CellTypeRefused(column_label=columns[first.key].label, why=first.why)
            queued = 0
            # Every node whose AI columns were written, once: the
            # barriers waiting on its path may now be complete for this
            # row. The node behind it is NOT run (see the module doc).
            # A plain column is on no path.
            nodes: set[str] = set()
            for key in verdict.written:
                column = columns[key]
                if isinstance(column, AiColumn):
                    nodes.add(column.node_id)
            reactions = WorkflowReactions(account_id=self.account_id)
            for node_id in sorted(nodes):
                queued += reactions.complete(
                    list_id=str(self.target_list.id), row_ids=[self.row_id], from_node_id=node_id
                )
            # As it now reads. The landing raised RowNotFound for a row
            # that was gone and holds the row's lock to the end of this
            # transaction, so the row is here.
            row = lists.row(self.target_list, self.row_id)
        return SetCellsReport(row=row, queued=queued)

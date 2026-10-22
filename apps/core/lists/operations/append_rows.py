"""THE way rows enter a sheet, whoever brings them: a push, a person
adding rows, an import, a snapshot. The rows land through the list
service's primitive AND the workflow is told of the ones that hold
something, in ONE transaction, so a row holding a value never exists
with no work queued to fill it and no door can forget the second
half. The primitive (ListService.add_rows) is called by nothing else
in production; a pin holds that.

Every door triggers, on purpose: a sheet that has no agent node yet
(a fresh import, a snapshot) is a no-op trigger, and a sheet that has
one is exactly the case where an arriving row must be judged.

A row that arrives BLANK (every cell empty: the sheet's add-rows
control makes such rows to be typed into) is created, counted and
ranked but not offered: nothing has arrived for an entry agent to
read, and a run on it would burn a model call to land a diagnosis and
count the row as attempted. It is offered when a person asks for a
column to fill (a fill whose prompt reads columns skips it as BLOCKED
until it holds something); an edit starts nothing, by the standing
rule."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction

from ..models import List, ListRow
from ..services.lists import ListService
from ..services.workflow_reactions import WorkflowReactions


@dataclass
class AppendReport:
    """What one append did: the rows created (with ids, for a caller
    that keys on them) and the runs the trigger queued for them."""

    created: list[ListRow]
    queued: int

    @property
    def added(self) -> int:
        return len(self.created)


class AppendRowsOperation:
    def __init__(self, *, account_id: str, target_list: List, rows: Sequence[dict[str, str]]) -> None:
        self.account_id = account_id
        self.target_list = target_list
        self.rows = list(rows)

    def run(self) -> AppendReport:
        """Raises the list service's ListNotFound / ListsFull as they are:
        each door answers them in its own voice."""
        with transaction.atomic():
            created = ListService(account_id=self.account_id).add_rows(self.target_list, self.rows)
            arrived = [row for row in created if _holds_something(row)]
            queued = WorkflowReactions(account_id=self.account_id).trigger(self.target_list, arrived)
        return AppendReport(created=created, queued=queued)


def _holds_something(row: ListRow) -> bool:
    """A row with at least one non-blank cell: the one kind the
    workflow is told about."""
    return any(str(value or "").strip() for value in row.data.values())

"""Landing a run on its row: the ONE writer of a resolved row.

A row is resolved when two writes land together: the sheet write
(ListService.write_cells: the values write-if-blank AND the cell
truth, one ListCellState per column the run owns, in one call so the
two can never disagree) and the run's close with its result stored on
it. They share ONE transaction on purpose: a task whose lease was
reclaimed mid-run must produce NOTHING, never a value from one attempt
wearing a diagnosis from another, so a close that misses rolls the
sheet write back with it. The lock order is ListRow, ListCellState,
NodeRun, the order both delete paths take (the reverse is an ABBA
deadlock against a mid-fill delete). What the landing unlocks
downstream (the workflow advance) is the processors' base's, after the
run's own transaction.

Two callers land rows, and before this module each restated the
writes: the consumer's terminal path, and its give-up past the
attempt cap (a run with no cells, its cause the last park's). What
differs between them is only HOW the task closes, so that is the one
thing a caller passes in. The counters DERIVE from the task rows and
cell states this writes, so nothing is folded back here.
"""

from __future__ import annotations

from typing import NamedTuple

from django.db import transaction

from openbower_schema.fills import CellRunResult

from ...constants import NodeRunStatus, StoredCellState
from ..cell_truth import CellTruth
from ..lists import ListService
from ..node_runs import NodeRunFlow


class LandingContext(NamedTuple):
    """Where a run lands: the sheet and the columns the run is
    responsible for. A fill-backed caller builds it from its fill job's
    consent (the column set it owns); the automatic path (autofill)
    from the task plus the agent's resolved column set."""

    account_id: str
    list_id: str
    column_keys: tuple[str, ...]


class ClaimLost(Exception):
    """The task's close missed (the lease was reclaimed mid-run):
    raised inside the landing's transaction so everything staged
    beside it, the value write included, rolls back with it."""


class Landed(NamedTuple):
    """What landing decided per column: which columns hold a value
    now, and the cause every other column carries."""

    answered: frozenset[str]
    declined: StoredCellState


def land_row(
    ctx: LandingContext,
    row_id: str,
    run_result: CellRunResult,
    *,
    truth: CellTruth,
    flow: NodeRunFlow,
    task_id: str,
    lists: ListService | None = None,
) -> Landed | None:
    """Write what a run produced onto its row under the truth its
    caller built, in one transaction, and settle the run DONE through
    `flow` with the result stored on it.
    Returns None when the settle missed (the lease was reclaimed):
    nothing was written. Raises the ListService's ListNotFound /
    RowNotFound as they are: a deleted sheet is the caller's story to
    resolve."""
    writer = lists or ListService(account_id=ctx.account_id)
    try:
        with transaction.atomic():
            written = writer.write_cells(
                ctx.list_id, row_id, dict(run_result.cells), column_keys=ctx.column_keys, truth=truth
            )
            # The close comes AFTER the sheet write (the lock order the
            # deletes share) and inside its transaction: a reclaimed
            # lease's miss rolls the sheet write back with it.
            if not flow.settle(task_id, result=run_result.model_dump(), status=NodeRunStatus.DONE):
                raise ClaimLost()
    except ClaimLost:
        return None
    return Landed(frozenset((*written.written, *written.occupied)), truth.declined_cause)

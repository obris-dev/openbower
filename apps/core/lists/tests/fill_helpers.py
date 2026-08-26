"""Simulate the worker's terminal writes THROUGH the queue seam.

A test that poked a task row directly would leave the sheet and the
diagnoses untouched, a state no real path can produce. These helpers
claim, write, and close the way the worker does, so every simulated
outcome exercises the same CAS and the same one-transaction write the
shipped worker runs.

`settle` with no cause writes a VALUE as well as the FILLED state,
because a filled cell is both: a value on the sheet row AND a record
saying a fill put it there. Recording one without the other is a state
no real path produces, and the two are written in one transaction.
"""

from __future__ import annotations

from django.utils import timezone

from ..constants import FillTaskStatus, StoredCellState
from ..models import Fill, FillTask, ListRow
from ..services import cell_truth
from ..services.fill_queue import FillQueueService
from ..services.lists import ListService

WORKER_ID = "test-seam"
# What a simulated fill writes into a cell it answers. Any non-blank
# value makes the cell filled; a recognisable one makes a failure
# readable.
FILLED_VALUE = "answered"


def _claim(fill: Fill, row_id: str) -> FillTask:
    """Lease the row's task the way claim_batch does (attempt counted
    at claim included), so the terminal CAS, which filters on the
    claimant's own stamp, can land."""
    task = FillTask.objects.get(fill_id=str(fill.id), row_id=row_id)
    FillTask.objects.filter(id=task.id).update(
        leased_by=WORKER_ID, leased_at=timezone.now(), attempts=task.attempts + 1, not_before=None
    )
    task.refresh_from_db()
    return task


def settle(fill_id: str, row_id: str, cause: StoredCellState | None = None, causes: dict | None = None) -> None:
    """One row's terminal write, seam-shaped.

    `cause` None means the run answered every column the fill owns (a
    value lands in each). `causes` is the per-column truth a partially
    answered run produces; omitted, `cause` speaks for every column.
    TRANSIENT parks instead of settling, because a park is not terminal.
    """
    fill = Fill.objects.get(id=fill_id)
    queue = FillQueueService(worker_id=WORKER_ID)
    task = _claim(fill, row_id)
    if cause == StoredCellState.TRANSIENT:
        assert queue.park_task(task, backoff_seconds=0), f"park missed for {fill_id}/{row_id}"
        return
    per_column = causes if causes is not None else ({} if cause is None else cell_truth.uniform(fill, cause))
    states = {key: per_column.get(key, StoredCellState.FILLED) for key in fill.column_keys}
    answered = [key for key, value in states.items() if value == StoredCellState.FILLED]
    if answered:
        ListService(account_id=fill.account_id, user_id=fill.user_id).write_cells(
            fill.list_id, row_id, dict.fromkeys(answered, FILLED_VALUE)
        )
    landed = queue.complete_task(fill, task, states=states, result={})
    assert landed, f"seam write missed for {fill_id}/{row_id}"


def settle_all(fill_id: str, cause: StoredCellState | None = None) -> None:
    """Every row the fill still owes, in sheet order."""
    for row_id in queued_row_ids(fill_id):
        settle(fill_id, row_id, cause)


def queued_row_ids(fill_id: str) -> list[str]:
    """The rows still owed, in sheet order: the QUEUE, which is what
    the fill will actually run next."""
    return [
        str(row_id)
        for row_id in FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.QUEUED)
        .order_by("position")
        .values_list("row_id", flat=True)
    ]


def targeted(fill_id: str) -> set[str]:
    """The row ids a fill targets, read from its QUEUE.

    The queue IS the consent record: one task per row the user agreed
    to, written at admission and never re-derived, so counting tasks is
    exactly what these assertions always meant."""
    return {str(row_id) for row_id in FillTask.objects.filter(fill_id=fill_id).values_list("row_id", flat=True)}


def targeted_positions(fill_id: str) -> list[int]:
    """Those rows' sheet positions, in sheet order."""
    return list(FillTask.objects.filter(fill_id=fill_id).order_by("position").values_list("position", flat=True))


def targeted_pairs(fill_id: str) -> list[tuple[str, int]]:
    """(row id, position) for the rows a fill targets, in sheet order."""
    return [
        (str(row_id), position)
        for row_id, position in FillTask.objects.filter(fill_id=fill_id)
        .order_by("position")
        .values_list("row_id", "position")
    ]


def row_value(list_id: str, row_id: str, column_key: str) -> str:
    """One cell's value, for assertions that care what landed."""
    return str(ListRow.objects.get(id=row_id, list_id=list_id).data.get(column_key, ""))

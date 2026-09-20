"""Simulate the consumer's terminal writes THROUGH the state machine,
and drive a fill job the way production does.

A test that poked a task row directly would leave the sheet and the
diagnoses untouched, a state no real path can produce. These helpers
claim, write, and close the way the shared consumer does (NodeRunFlow
claim -> land_row -> settle/park), so every simulated outcome exercises
the same CAS and the same one-transaction write the shipped consumer
runs.

`settle` with no cause writes a VALUE as well as the FILLED state,
because a filled cell is both: a value on the sheet row AND a record
saying a fill put it there. Recording one without the other is a state
no real path produces, and the two are written in one transaction.

A fill is a JOB: `tick_fill` wakes it and works one tick, the way the
jobs container does seconds later (the walk that queues its runs, then
the poll that completes it once they settle); `fill_status` reads the
wire's word for it.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, enqueue
from openbower_schema.lists import AiColumn

from ..constants import NON_TERMINAL_NODE_RUN_STATES, CellSource, NodeRunStatus, StoredCellState
from ..jobs.fill import FillJob
from ..models import ListRow, Node, NodeRun
from ..processors import WalkMode
from ..services import cell_truth, fill_progress, webhook_runs
from ..services.lists import ListService
from ..services.node_runs import NodeRunFlow
from ..services.workflows import agent_id_of

WORKER_ID = "test-seam"
# What a simulated fill writes into a cell it answers. Any non-blank
# value makes the cell filled; a recognisable one makes a failure
# readable.
FILLED_VALUE = "answered"


def consent_of(fill_run_id: str) -> FillJob:
    """The fill job's payload, typed."""
    return FillJob.model_validate(Job.objects.get(id=fill_run_id).payload)


def cursor_of(fill_run_id: str) -> FillJob.Progress:
    """The fill job's cursor, typed (the settled denominator rides it)."""
    return FillJob.Progress.model_validate(Job.objects.get(id=fill_run_id).progress)


def confirmed_row_count(fill_run_id: str) -> int:
    """The wire's denominator: the consent until the walk ends, then
    the runs it queued."""
    cursor = cursor_of(fill_run_id)
    return cursor.targeted if cursor.targeted_at else consent_of(fill_run_id).consented


def fill_status(fill_run_id: str) -> str:
    """The wire's word for the fill, derived exactly as the wire is."""
    job = Job.objects.get(id=fill_run_id)
    return fill_progress.status_of(job, started=fill_progress.started(fill_run_id))


def tick_fill(fill_run_id: str) -> None:
    """Wake the fill job (a poll parks it for a few seconds; the test
    cannot wait) and work one tick, as the jobs container does."""
    Job.objects.filter(id=fill_run_id).update(scheduled_at=None)
    JobRunner(worker_id="test:tick").tick()


def open_fill_job(
    *,
    account_id: str,
    user_id: str,
    list_id: str,
    node_id: str,
    agent_id: str,
    column_keys: list[str],
    consented: int,
    mode: WalkMode = WalkMode.FRESH,
    status: JobStatus = JobStatus.READY,
    targeted: bool = True,
    targeted_at: datetime | None = None,
    **scope,
) -> Job:
    """A fill job built at the model level, its walk already done when
    `targeted` (the runs are the test's to create), so a lifecycle test
    starts from a fill that is polling its runs."""
    job = enqueue(
        account_id,
        FillJob(
            list_id=list_id,
            node_id=node_id,
            agent_id=agent_id,
            column_keys=column_keys,
            mode=mode,
            consented=consented,
            until_position=scope.pop("until_position", consented),
            **scope,
        ),
        user_id=user_id,
        subject_id=list_id,
    )
    if targeted:
        cursor = FillJob.Progress(
            after_position=consented,
            offered=consented,
            targeted_at=targeted_at or timezone.now(),
            targeted=consented,
        )
        job.progress = cursor.model_dump(mode="json")
    job.status = status
    job.save(update_fields=["progress", "status", "updated_at"])
    return job


def _claim(fill_run_id: str, row_id: str) -> NodeRun:
    """Claim the row's task through the state machine (READY | QUEUED ->
    PROCESSING, attempt counted at claim), so the terminal CAS, which
    filters on the claimant's own stamp, can land."""
    task = NodeRun.objects.get(fill_run_id=fill_run_id, row_id=row_id)
    claimed = NodeRunFlow(worker_id=WORKER_ID).claim(str(task.id))
    assert claimed is not None, f"claim missed for {fill_run_id}/{row_id}"
    return claimed


def settle(
    fill_run_id: str,
    row_id: str,
    cause: StoredCellState | None = None,
    causes: dict | None = None,
    tools: dict[str, str] | None = None,
) -> None:
    """One row's terminal write, seam-shaped.

    `cause` None means the run answered every column the fill owns (a
    value lands in each). `causes` is the per-column truth a partially
    answered run produces; omitted, `cause` speaks for every column.
    `tools` is the run's per-tool provider statuses (empty by default).
    TRANSIENT parks instead of settling, because a park is not terminal.
    """
    job = Job.objects.get(id=fill_run_id)
    consent = FillJob.model_validate(job.payload)
    flow = NodeRunFlow(worker_id=WORKER_ID)
    task = _claim(fill_run_id, row_id)
    if cause == StoredCellState.TRANSIENT:
        assert flow.park(str(task.id), backoff_seconds=0, result={}), f"park missed for {fill_run_id}/{row_id}"
        return
    per_column = (
        causes if causes is not None else ({} if cause is None else cell_truth.uniform(consent.column_keys, cause))
    )
    states = {key: per_column.get(key, StoredCellState.FILLED) for key in consent.column_keys}
    answered = [key for key, value in states.items() if value == StoredCellState.FILLED]
    # A deliberate restatement of land_row (the per-column `causes`
    # it cannot express), in land_row's own lock order: ListRow,
    # NodeRun, ListCellState, one transaction.
    with transaction.atomic():
        if answered:
            ListService(account_id=job.account_id).write_cells(
                consent.list_id, row_id, dict.fromkeys(answered, FILLED_VALUE)
            )
        landed = flow.settle(str(task.id), {"tools": tools or {}}, status=NodeRunStatus.DONE)
        assert landed, f"seam write missed for {fill_run_id}/{row_id}"
        cell_truth.write(
            account_id=job.account_id,
            list_id=consent.list_id,
            row_id=row_id,
            fill_run_id=fill_run_id,
            states=states,
            tools=tools or {},
            source=CellSource.FILL,
        )
        webhook_runs.advance_row(
            account_id=job.account_id, list_id=consent.list_id, row_id=row_id, node_id=task.node_id
        )


def settle_all(fill_run_id: str, cause: StoredCellState | None = None) -> None:
    """Every row the fill still owes, in sheet order."""
    for row_id in queued_row_ids(fill_run_id):
        settle(fill_run_id, row_id, cause)


def queued_row_ids(fill_run_id: str) -> list[str]:
    """The rows still owed, in sheet order: the non-terminal tasks
    (READY, QUEUED, or PROCESSING), which is what the fill will actually
    run next."""
    return [
        str(row_id)
        for row_id in NodeRun.objects.filter(fill_run_id=fill_run_id, status__in=NON_TERMINAL_NODE_RUN_STATES)
        .order_by("position")
        .values_list("row_id", flat=True)
    ]


def targeted(fill_run_id: str) -> set[str]:
    """The row ids a fill targets, read from its QUEUE.

    The queue IS the consent record: one task per row the user agreed
    to, written by the fill job's walk and never re-derived, so counting
    tasks is exactly what these assertions always meant (a test ticks
    the jobs runner after admitting, as production does seconds after
    the click)."""
    return {str(row_id) for row_id in NodeRun.objects.filter(fill_run_id=fill_run_id).values_list("row_id", flat=True)}


def targeted_positions(fill_run_id: str) -> list[int]:
    """Those rows' sheet positions, in sheet order."""
    return list(NodeRun.objects.filter(fill_run_id=fill_run_id).order_by("position").values_list("position", flat=True))


def targeted_pairs(fill_run_id: str) -> list[tuple[str, int]]:
    """(row id, position) for the rows a fill targets, in sheet order."""
    return [
        (str(row_id), position)
        for row_id, position in NodeRun.objects.filter(fill_run_id=fill_run_id)
        .order_by("position")
        .values_list("row_id", "position")
    ]


def fill_agent_id(column: AiColumn) -> str:
    """The agent behind an AI column, through its node: the column
    binds to the node, the node names the agent."""
    return agent_id_of(Node.objects.get(id=column.node_id))


def row_value(list_id: str, row_id: str, column_key: str) -> str:
    """One cell's value, for assertions that care what landed."""
    return str(ListRow.objects.get(id=row_id, list_id=list_id).data.get(column_key, ""))

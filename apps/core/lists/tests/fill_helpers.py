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
from jobs.services import JobRunner, JobService
from openbower_schema.lists import AiColumn

from ..constants import NON_TERMINAL_NODE_RUN_STATES, CellSource, NodeRunStatus, StoredCellState
from ..jobs.fill import FillJob
from ..models import ListRow, Node, NodeRun
from ..processors import WalkMode
from ..serializers import fill_run_wire
from ..services import advance, fill_progress
from ..services.cell_truth import CellTruth
from ..services.fills import page_progress
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
    """The wire's denominator, read off the wire itself (never a second
    spelling of the serializer's rule)."""
    return fill_run_wire(Job.objects.get(id=fill_run_id))["confirmed_row_count"]


def fill_status(fill_run_id: str) -> str:
    """The wire's word for the fill, derived exactly as the wire is."""
    job = Job.objects.get(id=fill_run_id)
    return fill_progress.status_of(job, started=page_progress([job])[fill_run_id].started)


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
    covered: int | None = None,
    mode: WalkMode = WalkMode.FRESH,
    status: JobStatus = JobStatus.READY,
    targeted: bool = True,
    targeted_at: datetime | None = None,
    **scope,
) -> Job:
    """A fill job built at the model level, its walk already done when
    `targeted` (the runs are the test's to create), so a lifecycle test
    starts from a fill that is polling its runs. `consented` here is
    the count the test means; the job derives its own from `covered`
    and `max_row_count`."""
    job = JobService(account_id=account_id).enqueue(
        FillJob(
            list_id=list_id,
            node_id=node_id,
            agent_id=agent_id,
            column_keys=column_keys,
            mode=mode,
            covered=consented if covered is None else covered,
            until_id=scope.pop("until_id", ""),
            **scope,
        ),
        user_id=user_id,
        target_id=list_id,
    )
    if targeted:
        cursor = FillJob.Progress(
            after_id="",
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
    # `causes` names the columns the run did NOT answer; a run carries
    # one declined cause, so they all carry the same one.
    unanswered = set(causes) if causes is not None else (set(consent.column_keys) if cause is not None else set())
    declined = next(iter(causes.values())) if causes else (cause or StoredCellState.NO_EVIDENCE)
    answered = [key for key in consent.column_keys if key not in unanswered]
    truth = CellTruth(source=CellSource.AGENT, fill_run_id=fill_run_id, declined_cause=declined, tools=tools or {})
    # land_row's own shape and lock order: the sheet write (values and
    # truth as one), then the settle, one transaction, then the advance.
    with transaction.atomic():
        ListService(account_id=job.account_id).write_cells(
            consent.list_id, row_id, dict.fromkeys(answered, FILLED_VALUE), column_keys=consent.column_keys, truth=truth
        )
        landed = flow.settle(str(task.id), result={"tools": tools or {}}, status=NodeRunStatus.DONE)
        assert landed, f"seam write missed for {fill_run_id}/{row_id}"
    advance.advance_rows(account_id=job.account_id, list_id=consent.list_id, row_ids=[row_id], node_id=task.node_id)


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
        .order_by("rank", "id")
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


def row_numbers(list_id: str) -> dict[str, int]:
    """row id -> its 1-based number in SHEET ORDER (rank, id): what the
    gutter would show. Derived here exactly as a renderer derives it,
    since nothing stores a row number."""
    ordered = ListRow.objects.filter(list_id=list_id).order_by("rank", "id").values_list("id", flat=True)
    return {str(row_id): number for number, row_id in enumerate(ordered, start=1)}


def targeted_numbers(fill_run_id: str) -> list[int]:
    """The sheet numbers of the rows a fill targets, in sheet order."""
    return [number for _row_id, number in targeted_pairs(fill_run_id)]


def targeted_pairs(fill_run_id: str) -> list[tuple[str, int]]:
    """(row id, sheet number) for the rows a fill targets, in sheet order."""
    numbers = row_numbers(consent_of(fill_run_id).list_id)
    row_ids = [
        str(row_id) for row_id in NodeRun.objects.filter(fill_run_id=fill_run_id).values_list("row_id", flat=True)
    ]
    return sorted(((row_id, numbers[row_id]) for row_id in row_ids), key=lambda pair: pair[1])


def fill_agent_id(column: AiColumn) -> str:
    """The agent behind an AI column, through its node: the column
    binds to the node, the node names the agent."""
    return agent_id_of(Node.objects.get(id=column.node_id))


def row_value(list_id: str, row_id: str, column_key: str) -> str:
    """One cell's value, for assertions that care what landed."""
    return str(ListRow.objects.get(id=row_id, list_id=list_id).data.get(column_key, ""))

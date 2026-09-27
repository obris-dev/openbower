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
from django.urls import reverse
from django.utils import timezone

from agents.services import AgentService
from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, JobService
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.lists import AiColumn

from ..cells import AnsweredWrite, LandingContext, RowLanding, TypedWrite
from ..constants import NON_TERMINAL_NODE_RUN_STATES, CellSource, NodeRunStatus, StoredCellState
from ..jobs.fill import FillJob
from ..models import List, ListRow, Node, NodeRun
from ..nodes.column_agent import ColumnAgent
from ..nodes.wait_until import WaitUntil
from ..serializers import fill_run_wire
from ..services import fill_progress
from ..services.ai_columns import AiColumnService
from ..services.fill_admission import FillAdmissionService
from ..services.fills import page_progress
from ..services.lists import ListService
from ..services.node_runs import NodeRunFlow
from ..services.workflow_reactions import WorkflowReactions
from ..services.workflows import WorkflowService

WORKER_ID = "test-seam"
# What a simulated fill writes into a cell it answers. Any non-blank
# value makes the cell filled; a recognisable one makes a failure
# readable.
FILLED_VALUE = "answered"


def start_fill(
    list_id: str,
    *,
    account_id: str,
    user_id: str,
    config: AgentConfig | None = None,
    agent_id: str = "",
    max_row_count: int = 0,
) -> tuple[list[str], Job]:
    """The drawer's two requests: create the AI column set, then fill
    it (the first column names the fill). Returns the created keys and
    the fill, queued and not yet walked."""
    before = {column.key for column in List.objects.get(id=list_id).columns}
    ai_columns = AiColumnService(account_id=account_id, user_id=user_id)
    added = ai_columns.add(list_id, config=config, agent_id=agent_id)
    keys = [column.key for column in added.columns if column.key not in before]
    admission = FillAdmissionService(account_id=account_id, user_id=user_id)
    fill = admission.fill_column(list_id=list_id, column_key=keys[0], max_row_count=max_row_count)
    return keys, fill


def chain_behind(target_list: List, *, upstream_node_id: str, key: str) -> Node:
    """An AI column whose node stands behind a barrier: a path headed by
    a wait naming the upstream node's path, then a node for a REAL agent
    whose one output is `key`, and a column `key` bound to it. The shape
    no gesture builds yet, which every fill must refuse to start at; the
    agent is real so the entry-action check is the only refusal left."""
    config = AgentConfig(
        prompt="Find the answer for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="test-model",
        tools=AgentTools(),
        outputs=[AgentOutput(key=key, label=key.title(), type="text")],
    )
    agents = AgentService(account_id=target_list.account_id)
    agent = agents.create(owner_id=target_list.user_id, label=key.title(), config=config)
    workflows = WorkflowService(account_id=target_list.account_id)
    upstream = workflows.get_node(upstream_node_id)
    _, nodes = workflows.create_path(
        target_list,
        [WaitUntil(inbound_path_ids=[upstream.path_id]), ColumnAgent(agent_id=str(agent.id))],
    )
    chained = nodes[1]
    column = AiColumn(key=key, label=key.title(), type="text", node_id=str(chained.id))
    target_list.columns = [*target_list.columns, column]
    target_list.save(update_fields=["columns", "updated_at"])
    return chained


def post_ai_column(client, list_id: str, body: dict):
    """POST /v1/lists/{id}/columns/ai: the create alone (no fill)."""
    url = reverse("lists_columns_ai", kwargs={"id": list_id})
    return client.post(url, body, content_type="application/json")


def post_column_fill(client, list_id: str, key: str, max_row_count: int = 0):
    """POST /v1/lists/{id}/columns/{key}/fill, the scope key only when
    scoped (the drawer and the tracker send it that way)."""
    url = reverse("lists_column_fill", kwargs={"id": list_id, "key": key})
    body = {"max_row_count": max_row_count} if max_row_count else {}
    return client.post(url, body, content_type="application/json")


def created_keys(detail: dict) -> list[str]:
    """The keys a create made, read from its reply as the web reads them
    (createdColumnKeys): the trailing AI columns on the last column's
    node, since the create appends its columns at the end, on one node."""
    columns = detail["columns"]
    last = columns[-1]
    keys: list[str] = []
    for column in reversed(columns):
        if column["kind"] != "ai" or column["node_id"] != last["node_id"]:
            break
        keys.insert(0, column["key"])
    return keys


def consent_of(fill_run_id: str) -> FillJob:
    """The fill job's payload, typed."""
    return FillJob.model_validate(Job.objects.get(id=fill_run_id).payload)


def cursor_of(fill_run_id: str) -> FillJob.Progress:
    """The fill job's cursor, typed (the settled denominator rides it)."""
    return FillJob.Progress.model_validate(Job.objects.get(id=fill_run_id).progress)


def target_row_count(fill_run_id: str) -> int:
    """The wire's denominator, read off the wire itself (never a second
    spelling of the serializer's rule)."""
    return fill_run_wire(Job.objects.get(id=fill_run_id))["target_row_count"]


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
    target_row_count: int,
    covered: int | None = None,
    status: JobStatus = JobStatus.READY,
    targeted: bool = True,
    targeted_at: datetime | None = None,
    **scope,
) -> Job:
    """A fill job built at the model level, its walk already done when
    `targeted` (the runs are the test's to create), so a lifecycle test
    starts from a fill that is polling its runs. `target_row_count` here is
    the count the test means; the job derives its own from `covered`
    and `max_row_count`."""
    job = JobService(account_id=account_id).enqueue(
        FillJob(
            list_id=list_id,
            node_id=node_id,
            agent_id=agent_id,
            column_keys=column_keys,
            covered=target_row_count if covered is None else covered,
            until_id=scope.pop("until_id", ""),
            **scope,
        ),
        user_id=user_id,
        target_id=list_id,
    )
    if targeted:
        cursor = FillJob.Progress(
            after_id="",
            offered=target_row_count,
            targeted_at=targeted_at or timezone.now(),
            targeted=target_row_count,
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
    writes = [
        AnsweredWrite(key, None if key in unanswered else FILLED_VALUE, declined, tools=tools or {})
        for key in consent.column_keys
    ]
    # The processor's landing shape and lock order: the writes through
    # the one landing, then the settle, one transaction, then the advance.
    ctx = LandingContext(list_id=consent.list_id, source=CellSource.NODE, fill_run_id=fill_run_id)
    with transaction.atomic():
        ListService(account_id=job.account_id).land_row(ctx, RowLanding(row_id, writes))
        landed = flow.settle(str(task.id), result={"tools": tools or {}}, status=NodeRunStatus.DONE)
        assert landed, f"seam write missed for {fill_run_id}/{row_id}"
    WorkflowReactions(account_id=job.account_id).advance(
        list_id=consent.list_id, row_ids=[row_id], from_node_id=task.node_id
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


def row_value(list_id: str, row_id: str, column_key: str) -> str:
    """One cell's value, for assertions that care what landed."""
    return str(ListRow.objects.get(id=row_id, list_id=list_id).data.get(column_key, ""))


def type_cells(target_list: List, row_id: str, values: dict[str, str]) -> None:
    """A person typing values into cells, landed under MANUAL; a blank
    is nothing to write."""
    writes = [TypedWrite(key, value) for key, value in values.items() if value.strip()]
    ctx = LandingContext(list_id=str(target_list.id), source=CellSource.MANUAL, fill_run_id=None)
    ListService(account_id=target_list.account_id).land_row(ctx, RowLanding(row_id, writes))

"""Simulate the consumer's terminal writes THROUGH the state machine.

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
"""

from __future__ import annotations

from django.db import transaction

from openbower_schema.lists import AiColumn

from ..constants import NON_TERMINAL_NODE_RUN_STATES, CellSource, NodeRunStatus, StoredCellState
from ..models import Fill, ListRow, Node, NodeRun
from ..services import cell_truth, webhook_runs
from ..services.lists import ListService
from ..services.node_runs import NodeRunFlow
from ..services.workflows import agent_id_of

WORKER_ID = "test-seam"
# What a simulated fill writes into a cell it answers. Any non-blank
# value makes the cell filled; a recognisable one makes a failure
# readable.
FILLED_VALUE = "answered"


def _claim(fill: Fill, row_id: str) -> NodeRun:
    """Claim the row's task through the state machine (READY | QUEUED ->
    PROCESSING, attempt counted at claim), so the terminal CAS, which
    filters on the claimant's own stamp, can land."""
    task = NodeRun.objects.get(fill_run_id=str(fill.id), row_id=row_id)
    claimed = NodeRunFlow(worker_id=WORKER_ID).claim(str(task.id))
    assert claimed is not None, f"claim missed for {fill.id}/{row_id}"
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
    fill = Fill.objects.get(id=fill_run_id)
    flow = NodeRunFlow(worker_id=WORKER_ID)
    task = _claim(fill, row_id)
    if cause == StoredCellState.TRANSIENT:
        assert flow.park(str(task.id), backoff_seconds=0, result={}), f"park missed for {fill_run_id}/{row_id}"
        return
    per_column = causes if causes is not None else ({} if cause is None else cell_truth.uniform(fill, cause))
    states = {key: per_column.get(key, StoredCellState.FILLED) for key in fill.column_keys}
    answered = [key for key, value in states.items() if value == StoredCellState.FILLED]
    # A deliberate restatement of land_row (the per-column `causes`
    # it cannot express), in land_row's own lock order: ListRow,
    # NodeRun, ListCellState, one transaction.
    with transaction.atomic():
        if answered:
            ListService(account_id=fill.account_id).write_cells(
                fill.list_id, row_id, dict.fromkeys(answered, FILLED_VALUE)
            )
        landed = flow.settle(str(task.id), {"tools": tools or {}}, status=NodeRunStatus.DONE)
        assert landed, f"seam write missed for {fill_run_id}/{row_id}"
        cell_truth.write(
            account_id=fill.account_id,
            list_id=fill.list_id,
            row_id=row_id,
            fill_run_id=str(fill.id),
            config_fingerprint=fill.config_fingerprint,
            states=states,
            tools=tools or {},
            source=CellSource.FILL,
        )
        webhook_runs.advance_row(account_id=fill.account_id, list_id=fill.list_id, row_id=row_id, node_id=task.node_id)


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
    to, written by the walk admission queues and never re-derived, so
    counting tasks is exactly what these assertions always meant (a
    test ticks the jobs runner after admitting, as production does
    seconds after the click)."""
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

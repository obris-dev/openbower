"""A webhook node's runs on the ledger: how a row EARNS one (the advance,
run inside every terminal landing), what a run's stored result says,
and the two purges its owners call. A webhook run is a NodeRun of kind
webhook, born DEFERRED at the next window of its node's cadence, and
claimed by the flush (operations/flush_webhooks.py), never by the agent
worker.

The wait node is a barrier, not a ledger: when every column it waits
on is done for a row, the only effect is a run for each webhook node
behind it. Completion is re-read from the cells at claim time, so an
open run always sends the row's LATEST completion, and the open-run key
(one open automatic run per (row, node)) makes a re-completion while
one is pending a no-op.

Trusted-process module like node_runs.py: account ids are passed in.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel

from ..constants import NodeRunStatus, WebhookRunOutcome
from ..models import List, ListRow, Node, NodeRun
from ..nodes.registry import WEBHOOK
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from .cell_states import CellStateService
from .digest_payload import completion_of
from .webhook_paths import wait_keys_for
from .workflows import WorkflowService, config_as


def next_window(now: datetime, interval_seconds: int) -> datetime:
    """The next boundary of an interval on the epoch clock, strictly
    after `now`: every row completing inside one window lands on the
    same boundary and so rides one digest. Second resolution; a `now`
    exactly on a boundary yields the boundary after it."""
    boundary = (int(now.timestamp()) // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


class WebhookRunResult(BaseModel):
    """The shape of a webhook run's `result`, typed both ways: written
    with `model_dump()`, read with `model_validate`."""

    outcome: WebhookRunOutcome
    delivery_id: str = ""
    error: str = ""


def wait_keys_of(*, account_id: str, target_list: List, wait: WaitUntil) -> list[str]:
    """The columns a wait node waits on, in sheet order: its inbound
    paths resolved to the agent nodes ending them, then to the columns
    those nodes fill. A path that no longer resolves drops out (the
    wait is judged on what remains); a wait whose paths ALL fail to
    resolve yields no keys, and every caller treats that as nothing to
    judge rather than as complete."""
    agent_nodes = Node.objects.filter(account_id=account_id, path_id__in=wait.inbound_path_ids)
    node_by_path = {node.path_id: str(node.id) for node in agent_nodes}
    return wait_keys_for(wait.inbound_path_ids, columns=target_list.columns, node_by_path=node_by_path)


def webhook_nodes_on(workflows: WorkflowService, path_id: str) -> list[Node]:
    """The webhook nodes behind a wait node, in rank order."""
    return [node for node in workflows.nodes_on_path(path_id) if node.kind == WEBHOOK]


def runs_for_complete_row(
    *, account_id: str, list_id: str, row: ListRow, webhook_nodes: list[Node], now: datetime
) -> list[NodeRun]:
    """One DEFERRED run per webhook node for a row that just completed,
    each at the next window of its node's cadence. Unsaved."""
    runs: list[NodeRun] = []
    for node in webhook_nodes:
        webhook = config_as(node, Webhook)
        runs.append(
            NodeRun(
                account_id=account_id,
                fill_run_id=None,
                node_id=str(node.id),
                kind=WEBHOOK,
                row_id=str(row.id),
                list_id=list_id,
                position=row.position,
                status=NodeRunStatus.DEFERRED,
                not_before=next_window(now, webhook.interval_seconds),
                queued_at=now,
                last_state_change_at=now,
            )
        )
    return runs


def advance_row(*, account_id: str, list_id: str, row_id: str, node_id: str, now: datetime | None = None) -> int:
    """The fan-in check after a node lands on a row: for every wait node
    naming the landed node's path, if the row is now complete for the
    columns that wait waits on, enqueue a run for each webhook node
    behind it. Returns the runs offered (a re-completion while one is
    open inserts nothing under the open-run key). A node with no path
    (the bench) or a path no wait names returns at the first read, so
    the common landing pays one node read and one indexed query."""
    now = now or timezone.now()
    landed = Node.objects.filter(id=node_id, account_id=account_id).only("path_id").first()
    if landed is None or not landed.path_id:
        return 0
    workflows = WorkflowService(account_id=account_id)
    waits = list(workflows.wait_nodes_naming(landed.path_id))
    if not waits:
        return 0
    target_list = List.objects.filter(id=list_id, account_id=account_id).first()
    row = ListRow.objects.filter(id=row_id, list_id=list_id).only("id", "position").first()
    if target_list is None or row is None:
        return 0
    cell_states = CellStateService(account_id=account_id)
    runs: list[NodeRun] = []
    for wait_node in waits:
        wait = config_as(wait_node, WaitUntil)
        wait_keys = wait_keys_of(account_id=account_id, target_list=target_list, wait=wait)
        if not wait_keys:
            continue
        states = cell_states.iter_states(list_id, row_id=row_id, column_keys=wait_keys)
        records = {key: (state, updated_at) for key, state, updated_at in states}
        if completion_of(records, wait_keys) is None:
            continue
        webhook_nodes = webhook_nodes_on(workflows, wait_node.path_id)
        runs.extend(
            runs_for_complete_row(account_id=account_id, list_id=list_id, row=row, webhook_nodes=webhook_nodes, now=now)
        )
    if not runs:
        return 0
    NodeRun.objects.bulk_create(runs, ignore_conflicts=True)
    return len(runs)


def purge_for_node(node_id: str) -> int:
    """A webhook column's runs go with it: called by the column delete
    before the path, unconditionally (a gone node still has runs by
    id). Returns the count removed."""
    with transaction.atomic():
        removed, _by_model = NodeRun.objects.filter(kind=WEBHOOK, node_id=node_id).delete()
    return removed


def purge_for_list(list_id: str) -> int:
    """A sheet's webhook runs go with it, beside its fill-backed runs."""
    with transaction.atomic():
        removed, _by_model = NodeRun.objects.filter(kind=WEBHOOK, list_id=list_id).delete()
    return removed

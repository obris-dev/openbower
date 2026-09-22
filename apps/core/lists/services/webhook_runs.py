"""A webhook node's runs on the ledger: how a row EARNS one (the advance,
run inside every terminal landing), what a run's stored result says,
the cell word read off the newest run, and the two purges its owners
call. A webhook run is a NodeRun of kind webhook, born DEFERRED at the
next window of its node's cadence (the WebhookProcessor's judgement),
and claimed by the flush (operations/flush_deferred.py), never by the
agent worker.

The wait node is a barrier, not a ledger: when every column it waits
on is done for a row, the only effect is a run for each webhook node
behind it. Completion is re-read from the cells at claim time, so an
open run always sends the row's LATEST completion, and the open-run key
(one open automatic run per (row, node)) makes a re-completion while
one is pending a no-op.

Trusted-process module like node_runs.py: account ids are passed in.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel

from ..constants import NON_TERMINAL_NODE_RUN_STATES, NodeRunStatus, WebhookCellWord, WebhookRunOutcome
from ..models import List, ListRow, Node, NodeRun
from ..nodes.registry import WEBHOOK
from ..processors import processor_for
from .workflows import WorkflowService


class WebhookRunResult(BaseModel):
    """The shape of a webhook run's `result`, typed both ways: written
    with `model_dump()`, read with `model_validate`."""

    outcome: WebhookRunOutcome
    delivery_id: str = ""
    error: str = ""


def webhook_nodes_on(workflows: WorkflowService, path_id: str) -> list[Node]:
    """The webhook nodes behind a wait node, in rank order."""
    return [node for node in workflows.nodes_on_path(path_id) if node.kind == WEBHOOK]


def advance_row(*, account_id: str, list_id: str, row_id: str, node_id: str, now: datetime | None = None) -> int:
    """The fan-in check after a node lands on a row: every webhook node
    behind a wait node naming the landed node's path is offered the
    row, and its processor decides whether the row is owed a run now.
    Returns the runs offered (a re-completion while one is open inserts
    nothing under the open-run key). A node with no path (the preview) or
    a path no wait names returns at the first read, so the common
    landing pays one node read and one indexed query."""
    now = now or timezone.now()
    landed = Node.objects.filter(id=node_id, account_id=account_id).only("path_id").first()
    if landed is None or not landed.path_id:
        return 0
    workflows = WorkflowService(account_id=account_id)
    waits = list(workflows.wait_nodes_naming(landed.path_id))
    if not waits:
        return 0
    target_list = List.objects.filter(id=list_id, account_id=account_id).first()
    row = ListRow.objects.filter(id=row_id, list_id=list_id).only("id", "rank").first()
    if target_list is None or row is None:
        return 0
    offered = 0
    for wait_node in waits:
        for webhook_node in webhook_nodes_on(workflows, wait_node.path_id):
            processor = processor_for(account_id=account_id, node=webhook_node)
            offered += processor.enqueue_runs(target_list, [row], now=now)
    return offered


def cell_words_for(
    *, account_id: str, node_ids: Sequence[str], row_ids: Sequence[str]
) -> dict[tuple[str, str], WebhookCellWord]:
    """(row id, node id) -> the cell's word for a page of rows, off each
    pair's NEWEST run (ids are time-ordered), in one query served by
    `node_run_cell_idx`. An open run says waiting; a DONE run
    says what its result says (a run parked mid-retry is open, so it
    reads waiting too); a run retired because its row or list went
    missing says nothing."""
    if not node_ids or not row_ids:
        return {}
    newest = (
        NodeRun.objects.filter(
            account_id=account_id, kind=WEBHOOK, node_id__in=list(node_ids), row_id__in=list(row_ids)
        )
        .order_by("row_id", "node_id", "-id")
        .distinct("row_id", "node_id")
        .values_list("row_id", "node_id", "status", "result")
    )
    words: dict[tuple[str, str], WebhookCellWord] = {}
    for row_id, node_id, status, result in newest:
        if status in NON_TERMINAL_NODE_RUN_STATES:
            words[(row_id, node_id)] = WebhookCellWord.WAITING
        elif status == NodeRunStatus.DONE:
            outcome = WebhookRunResult.model_validate(result).outcome
            words[(row_id, node_id)] = (
                WebhookCellWord.SENT if outcome == WebhookRunOutcome.SENT else WebhookCellWord.FAILED
            )
    return words


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

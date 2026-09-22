"""The workflow advance: a node has landed on a row, so which nodes
does that landing unlock for the row? Every wait node naming the
landed node's path is a barrier that row may now have cleared; every
node standing behind such a barrier is OFFERED the row, and its own
processor judges whether the row is owed a run now (the webhook kind
re-reads the row's completion over the barrier's columns; a kind on
a structural pass may decline). The barrier itself is never offered:
it gates, it does no work of its own.

Run inside every terminal landing, last in its transaction: it only
inserts (the open-run key makes a re-offer a no-op), so it holds no
existing row lock, and it judges from the cell truth written just
before it. Trusted-process module like node_runs.py: account ids are
passed in."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from django.utils import timezone

from ..models import List, ListRow, Node
from .workflows import WorkflowService


def advance_rows(
    *, account_id: str, list_id: str, row_ids: Sequence[str], node_id: str, now: datetime | None = None
) -> int:
    """Offer the rows to every node behind a barrier the landed node's
    path feeds, as ONE page per node (a processor's judgement batches
    its reads per page, so a settled batch costs each downstream node
    one offer, not one per row). Returns the runs enqueued. A node
    with no path (the preview node) or a path no wait names returns at
    the first read, so the common landing pays one node read and one
    indexed query."""
    # Function-local: the processors' base calls this after every run,
    # and this reaches back into the processors for each offered node.
    from ..processors import processor_for

    now = now or timezone.now()
    landed = Node.objects.filter(id=node_id, account_id=account_id).only("path_id").first()
    if landed is None or not landed.path_id:
        return 0
    workflows = WorkflowService(account_id=account_id)
    waits = list(workflows.wait_nodes_naming(landed.path_id))
    if not waits:
        return 0
    target_list = List.objects.filter(id=list_id, account_id=account_id).first()
    if target_list is None:
        return 0
    rows = list(ListRow.objects.filter(id__in=list(row_ids), list_id=list_id).only("id", "rank").order_by("rank", "id"))
    if not rows:
        return 0
    enqueued = 0
    for wait in waits:
        for node in workflows.nodes_on_path(wait.path_id):
            if str(node.id) == str(wait.id):
                continue
            enqueued += processor_for(account_id=account_id, node=node).enqueue_runs(target_list, rows, now=now)
    return enqueued

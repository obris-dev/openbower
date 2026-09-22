"""The workflow advance: a node has landed on some rows, so what does
the workflow owe those rows next? Two rules, and nothing else:

1. The NEXT node on the landed node's own path (the next rank key) is
   offered the rows.
2. When the landed node was the LAST on its path, every wait node
   naming that path as inbound is a barrier the rows may now have
   cleared: the rows complete for the barrier (every column the
   barrier's inbound paths end in holds a done cell state) are offered
   to the node right after the wait on its path. The chain then
   continues by rule 1 as that node lands.

The barrier is judged HERE, per row, from cell truth, never from a
count of traversals: a row that completes in any order, or completes
twice, is judged right whenever any inbound node lands. The wait
itself is never offered (it gates, it does no work). The offered node's
processor judges its own owed-ness (whether the completion is newer
than its newest run, whether the row is eligible), so the advance is
an OFFER and a repeat is a no-op under the open-run key.

Run by the processors' base after every run that LANDED, after the
kind's own transaction. Trusted-process module like node_runs.py:
account ids are passed in."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from django.utils import timezone

from ..models import List, ListRow, Node
from ..nodes.wait_until import WaitUntil
from .cell_states import CellStateService
from .digest_payload import completion_of
from .webhook_paths import wait_keys_for
from .workflows import WorkflowService, config_as


def advance_rows(
    *, account_id: str, list_id: str, row_ids: Sequence[str], node_id: str, now: datetime | None = None
) -> int:
    """Offer the rows to what the workflow owes them next (the two
    rules above), as ONE page per offered node. Returns the runs
    enqueued. A node with no path (the preview node), or a last node
    no wait names, returns at the first reads, so the common landing
    pays one node read and one indexed query."""
    now = now or timezone.now()
    landed = Node.objects.filter(id=node_id, account_id=account_id).only("path_id", "rank").first()
    if landed is None or not landed.path_id:
        return 0
    workflows = WorkflowService(account_id=account_id)
    following = workflows.node_after(landed.path_id, landed.rank)
    if following is not None:
        return _offer(account_id, list_id, row_ids, following, now=now)
    waits = list(workflows.wait_nodes_naming(landed.path_id))
    if not waits:
        return 0
    target_list = List.objects.filter(id=list_id, account_id=account_id).first()
    if target_list is None:
        return 0
    enqueued = 0
    for wait in waits:
        behind = workflows.node_after(wait.path_id, wait.rank)
        if behind is None:
            continue
        cleared = _rows_clearing(account_id, target_list, row_ids, wait, workflows)
        enqueued += _offer(account_id, list_id, cleared, behind, now=now, target_list=target_list)
    return enqueued


def _offer(
    account_id: str, list_id: str, row_ids: Sequence[str], node: Node, *, now: datetime, target_list: List | None = None
) -> int:
    # Function-local: the processors' base calls the advance after every
    # run, and the advance reaches back into the processors here.
    from ..processors import processor_for

    if not row_ids:
        return 0
    target_list = target_list or List.objects.filter(id=list_id, account_id=account_id).first()
    if target_list is None:
        return 0
    rows = list(ListRow.objects.filter(id__in=list(row_ids), list_id=list_id).only("id", "rank").order_by("rank", "id"))
    if not rows:
        return 0
    return processor_for(account_id=account_id, node=node).enqueue_runs(target_list, rows, now=now)


def _rows_clearing(
    account_id: str, target_list: List, row_ids: Sequence[str], wait: Node, workflows: WorkflowService
) -> list[str]:
    """The rows complete for the barrier: every column the barrier's
    inbound paths END in holds a done cell state. A barrier whose paths
    resolve to no column waits on nothing, and no row clears it."""
    barrier = config_as(wait, WaitUntil)
    ending = workflows.nodes_ending(barrier.inbound_path_ids)
    node_by_path = {str(node.path_id): str(node.id) for node in ending}
    keys = wait_keys_for(barrier.inbound_path_ids, columns=target_list.columns, node_by_path=node_by_path)
    if not keys:
        return []
    records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
    cells = CellStateService(account_id=account_id)
    for row_id, column_key, state, updated_at in cells.iter_records(
        str(target_list.id), row_ids=row_ids, column_keys=keys
    ):
        records[row_id][column_key] = (state, updated_at)
    return [row_id for row_id in row_ids if completion_of(records.get(row_id, {}), keys) is not None]

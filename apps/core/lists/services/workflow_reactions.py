"""How the workflow MOVES: its reactions to the two changes a sheet
can undergo, each answered by offering rows to the nodes that care
(a processor's `enqueue_runs`, which judges for itself; an offer under
the open-run key repeats as a no-op).

TRIGGER: rows arrived on the sheet, so the workflow starts for them.
Every agent node the sheet's columns bind to judges the new rows under
PUSHED (the node runs unless the rows arrived with every column it
fills already valued). Called by the doors whose arrival is a request
to fill: a push, a person adding rows. A bulk load (an import, a
snapshot) does not call it: the Fill button is the consent to spend.

ADVANCE: a node landed on rows, so the workflow moves one step for
them. Two rules, and nothing else:

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
itself is never offered (it gates, it does no work). Called by the
processors' base after every run that LANDED, after the kind's own
transaction.

Reads the workflow at rest through WorkflowService and writes runs
through the processors: the one service that imports both, so the
graph stays one-directional (workflows -> reactions -> processors).
Account-scoped like every lists service."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from django.utils import timezone

from ..models import List, ListRow, Node
from ..nodes.wait_until import WaitUntil
from ..processors import WalkMode, WalkScope, processor_for
from .cell_states import CellStateService
from .digest_payload import completion_of
from .webhook_paths import wait_keys_for
from .workflows import WorkflowService, columns_by_node, config_as


class WorkflowReactions:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id
        self.workflows = WorkflowService(account_id=account_id)

    def trigger(self, target_list: List, rows: Sequence[ListRow]) -> int:
        """Rows arrived: start the workflow for them. Returns the runs
        queued (an already-queued (row, node) is a no-op under the
        open-run key, so a redelivery under-counts; callers ignore it).
        Rides the caller's transaction, so rows and their work commit
        together or not at all."""
        keys_by_node = columns_by_node(target_list)
        if not keys_by_node or not rows:
            return 0
        now = timezone.now()
        queued = 0
        agent_node_ids = list(keys_by_node.keys())
        for node in self.workflows.nodes_by_id(agent_node_ids):
            # The columns the judgement looks at ride the scope: the
            # starter decides them, the processor never reads the sheet
            # for them.
            scope = WalkScope(mode=WalkMode.PUSHED, column_keys=keys_by_node[str(node.id)])
            processor = processor_for(account_id=self.account_id, node=node, scope=scope)
            queued += processor.enqueue_runs(target_list, rows, now=now)
        return queued

    def advance(self, *, list_id: str, row_ids: Sequence[str], from_node_id: str, now: datetime | None = None) -> int:
        """A node landed on rows: move the workflow one step for them
        (the two rules above), as ONE page per offered node. Returns the
        runs enqueued. A node with no path (the preview node), or a
        last node no wait names, returns at the first reads, so the
        common landing pays one node read and one indexed query."""
        now = now or timezone.now()
        landed = Node.objects.filter(id=from_node_id, account_id=self.account_id).only("path_id", "rank").first()
        if landed is None or not landed.path_id:
            return 0
        following = self.workflows.node_after(landed.path_id, landed.rank)
        if following is not None:
            return self._offer(list_id, row_ids, following, now=now)
        waits = list(self.workflows.wait_nodes_naming(landed.path_id))
        if not waits:
            return 0
        target_list = List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            return 0
        enqueued = 0
        for wait in waits:
            behind = self.workflows.node_after(wait.path_id, wait.rank)
            if behind is None:
                continue
            cleared = self._rows_clearing(target_list, row_ids, wait)
            enqueued += self._offer(list_id, cleared, behind, now=now, target_list=target_list)
        return enqueued

    def _offer(
        self, list_id: str, row_ids: Sequence[str], node: Node, *, now: datetime, target_list: List | None = None
    ) -> int:
        if not row_ids:
            return 0
        target_list = target_list or List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            return 0
        rows = list(
            ListRow.objects.filter(id__in=list(row_ids), list_id=list_id).only("id", "rank").order_by("rank", "id")
        )
        if not rows:
            return 0
        return processor_for(account_id=self.account_id, node=node).enqueue_runs(target_list, rows, now=now)

    def _rows_clearing(self, target_list: List, row_ids: Sequence[str], wait: Node) -> list[str]:
        """The rows complete for the barrier: every column the barrier's
        inbound paths END in holds a done cell state. A barrier whose
        paths resolve to no column waits on nothing, and no row clears
        it."""
        barrier = config_as(wait, WaitUntil)
        ending = self.workflows.nodes_ending(barrier.inbound_path_ids)
        node_by_path = {str(node.path_id): str(node.id) for node in ending}
        keys = wait_keys_for(barrier.inbound_path_ids, columns=target_list.columns, node_by_path=node_by_path)
        if not keys:
            return []
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cells = CellStateService(account_id=self.account_id)
        for row_id, column_key, state, updated_at in cells.iter_records(
            str(target_list.id), row_ids=row_ids, column_keys=keys
        ):
            records[row_id][column_key] = (state, updated_at)
        return [row_id for row_id in row_ids if completion_of(records.get(row_id, {}), keys) is not None]

"""How the workflow MOVES: its reactions to the two changes a sheet
can undergo, each answered by offering rows to the nodes that care
(a processor's `enqueue_runs`, which judges for itself; an offer under
the open-run key repeats as a no-op).

TRIGGER: rows arrived on the sheet, so the workflow starts for them.
The node behind each ENTRY marker (the head of a path nothing feeds)
judges the new rows under AUTOFILL: it runs unless they arrived with
every column it fills already valued. A node standing behind a WAIT is
not started by an arrival; its barrier is what starts it, through the
advance. Called by every door rows enter through
(operations/append_rows.py); a sheet with no workflow starts nothing,
and none is minted here.

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
twice, is judged right whenever any inbound node lands. NO MARKER is
ever offered (a marker says how a path is entered or waited for; it
does no work), and a reaction that finds one standing where work
belongs has no rule for it, so it stops there. Called by the
processors' base after every run that LANDED, after the kind's own
transaction.

Reads the workflow at rest through WorkflowService and writes runs
through the processors: the one service that imports both, so the
graph stays one-directional (workflows -> reactions -> processors).
Account-scoped like every lists service."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from django.utils import timezone

from ..models import List, ListRow, Node
from ..nodes.registry import HEAD_OF_PATH_MARKERS
from ..nodes.wait_until import WaitUntil
from ..processors import FillMode, FillScope, processor_for
from .cell_states import CellStateService
from .digest_payload import completion_of
from .webhook_paths import wait_keys_for
from .workflows import WorkflowService, columns_for_node, config_as

logger = logging.getLogger(__name__)


class WorkflowReactions:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id
        self.workflows = WorkflowService(account_id=account_id)

    def trigger(self, target_list: List, rows: Sequence[ListRow]) -> int:
        """Rows arrived: start the workflow for them, at the node behind
        each entry marker. Returns the runs queued (an already-queued
        (row, node) is a no-op under the open-run key, so a redelivery
        under-counts; callers ignore it). Rides the caller's
        transaction, so rows and their work commit together or not at
        all."""
        if not rows:
            return 0
        workflow = self.workflows.workflow_of(str(target_list.id))
        if workflow is None:
            return 0
        now = timezone.now()
        queued = 0
        for entry in self.workflows.iter_entry_nodes(str(workflow.id)):
            behind = self.workflows.node_after(entry.path_id, entry.rank)
            if behind is None:
                # A marker with nothing behind it starts nothing.
                continue
            queued += self._hand_to(behind, target_list, rows, now=now)
        return queued

    def advance(self, *, list_id: str, row_ids: Sequence[str], from_node_id: str, now: datetime | None = None) -> int:
        """A node landed on rows: move the workflow one step for them
        (the two rules above), as ONE page per offered node. Returns the
        runs enqueued. A node with no path (the preview node), or a
        last node no wait names, returns at the first reads, so the
        common landing pays one node read and one indexed query."""
        now = now or timezone.now()
        landed = (
            Node.objects.filter(id=from_node_id, account_id=self.account_id)
            .only("workflow_id", "path_id", "rank")
            .first()
        )
        if landed is None or not landed.path_id:
            return 0
        following = self.workflows.node_after(landed.path_id, landed.rank)
        if following is not None:
            # RULE 1, the step WITHIN a path: something stands behind the
            # landed node on its own path, so that node is the step and
            # every row that landed takes it. A path's own order is the
            # only thing gating it, so no barrier is read here.
            return self._queue_runs(list_id, row_ids, following, now=now)
        # RULE 2, the step OUT of a path: nothing stands behind the
        # landed node, so its path just ended, and the only way the
        # workflow moves on is into the barriers that path feeds.
        return self._advance_through_barriers(list_id, row_ids, landed, now=now)

    def _advance_through_barriers(self, list_id: str, row_ids: Sequence[str], landed: Node, *, now: datetime) -> int:
        """Rule 2: the landed node ENDED its path, so every barrier that
        path feeds is judged for the rows, and the node behind each
        barrier a row cleared is handed that row. A path no barrier
        waits on ends the workflow there (the common case, and it costs
        one indexed read to learn).

        Rows are judged PER BARRIER, not once: two barriers over the
        same path wait on different column sets, so a row can clear one
        and not the other. The `node_after` here is the same reader rule
        1 uses, asked of a DIFFERENT path: it steps past the barrier's
        own marker on the path the barrier heads, never further along
        the path that just ended."""
        waits = list(self.workflows.waits_on(landed.workflow_id, landed.path_id))
        if not waits:
            return 0
        target_list = List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            return 0
        enqueued = 0
        for wait in waits:
            behind = self.workflows.node_after(wait.path_id, wait.rank)
            if behind is None:
                # A barrier with nothing behind it gates nothing.
                continue
            cleared = self._rows_clearing(target_list, row_ids, wait)
            enqueued += self._queue_runs(list_id, cleared, behind, now=now, target_list=target_list)
        return enqueued

    def _refuse_marker(self, node: Node) -> bool:
        """Whether this node is a marker that should HEAD a path standing
        somewhere else, so the reaction stops here. A marker never runs,
        so one reached behind work is a path whose shape no rule covers.
        Stops rather than walking past, because walking past a wait would
        hand its rows on without the barrier it exists to be. Loud but
        never raising: the run that got here already landed and settled
        DONE, so an exception would only miss the settle CAS and blame
        the crash on a run that did its job. A marker that legitimately
        ENDS a path is not this set and not this rule: it needs its own,
        written above this line, or it lands in the factory."""
        if node.kind not in HEAD_OF_PATH_MARKERS:
            return False
        logger.error(
            "workflow reaction reached %s marker %s at rank %s on path %s, where work belongs",
            node.kind,
            node.id,
            node.rank,
            node.path_id,
        )
        return True

    def _hand_to(self, node: Node, target_list: List, rows: Sequence[ListRow], *, now: datetime) -> int:
        """Hand a node the rows and let its processor queue the runs it
        owes them, returning how many that was: a node handed rows may
        owe none, since the judgement is the processor's and never a
        reaction's. The ONE place a reaction reaches a processor, so the
        marker refusal is asked once however the reaction got here.

        The occasion is always AUTOFILL, whichever reaction this is: the
        sheet moved on its own, and the node judges what it is missing.
        Derived here rather than asked of the caller, because a caller
        that forgets does not fail, it silently hands over the default
        occasion (a structural walk, which an agent answers with no rows
        at all), and a barrier becomes a dead end for every kind that
        reads the occasion."""
        if not rows:
            return 0
        if self._refuse_marker(node):
            return 0
        # The columns the judgement looks at: the node's own on this
        # sheet, never read by the processor itself.
        keys = columns_for_node(target_list, str(node.id))
        scope = FillScope(mode=FillMode.AUTOFILL, column_keys=list(keys))
        processor = processor_for(account_id=self.account_id, node=node, scope=scope)
        return processor.enqueue_runs(target_list, rows, now=now)

    def _queue_runs(
        self, list_id: str, row_ids: Sequence[str], node: Node, *, now: datetime, target_list: List | None = None
    ) -> int:
        """The advance's shape of the same hand-over: a run names its row
        by id, so the sheet and the rows those ids name are read here,
        in SHEET order (a processor pages them as the sheet reads).
        Reads nothing when there are no ids to read for."""
        if not row_ids:
            return 0
        target_list = target_list or List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            return 0
        rows = list(
            ListRow.objects.filter(id__in=list(row_ids), list_id=list_id).only("id", "rank").order_by("rank", "id")
        )
        return self._hand_to(node, target_list, rows, now=now)

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

"""The webhook kind's processor: a row is owed a run once every column
its wait barrier waits on is done, and the run is born DEFERRED at the
next boundary of the node's cadence, so every row completing inside
one window rides one digest. The barrier is the wait node ahead of the
webhook on its path; this is the ONE place that resolves it to columns
(the flush, the backfill, the advance, and the column's config read all
ask here). The walk scope is irrelevant to this kind: a webhook judges
every pass the same way."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

from ..constants import NodeRunStatus
from ..models import List, ListRow, NodeRun
from ..nodes.registry import WEBHOOK
from ..nodes.webhook import Webhook
from ..services.cell_states import CellStateService
from ..services.digest_payload import completion_of
from ..services.webhook_paths import wait_keys_for
from ..services.workflows import NodeNotFound, WorkflowService, config_as
from .base import NodeProcessor
from .factory import register


def next_window(now: datetime, interval_seconds: int) -> datetime:
    """The next boundary of an interval on the epoch clock, strictly
    after `now`. Second resolution; a `now` exactly on a boundary
    yields the boundary after it."""
    boundary = (int(now.timestamp()) // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


class WebhookProcessor(NodeProcessor):
    KIND: ClassVar[str] = WEBHOOK

    def wait_keys(self, target_list: List) -> list[str]:
        """The columns the barrier ahead of this node waits on, in sheet
        order: its inbound paths resolved to the agent nodes ending
        them, then to the columns those nodes fill on this sheet. A
        path that no longer resolves drops out; a barrier that is gone,
        or resolves to nothing, waits on nothing, and no row is ever
        complete for it. This kind's own accessor (the flush and the
        column's config read need the keys themselves), not part of the
        walker's contract."""
        workflows = WorkflowService(account_id=self.account_id)
        try:
            wait = workflows.wait_ahead_of(self.node)
        except NodeNotFound:
            return []
        agent_nodes = workflows.nodes_ending(wait.inbound_path_ids)
        node_by_path = {node.path_id: str(node.id) for node in agent_nodes}
        return wait_keys_for(wait.inbound_path_ids, columns=target_list.columns, node_by_path=node_by_path)

    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime) -> int:
        """A row is owed a run when it is complete for the barrier's
        columns AND that completion is newer than the newest run this
        node already holds for it. The open-run key alone guards only
        OPEN runs; without the second test a walker re-offering a page
        after the flush sent it (a reclaimed slice, a second backfill)
        would send the same completion twice. A LATER completion still
        opens a new run. Two reads per page: the cell records over the
        barrier's columns, and the newest run per row."""
        wait_keys = self.wait_keys(target_list)
        if not wait_keys or not rows:
            return 0
        list_id = str(target_list.id)
        row_ids = [str(row.id) for row in rows]
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=self.account_id)
        for row_id, column_key, state, updated_at, _fingerprint in cell_states.iter_records(
            list_id, row_ids=row_ids, column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)
        covered = self._newest_run_at(row_ids)
        webhook = config_as(self.node, Webhook)
        window = next_window(now, webhook.interval_seconds)
        runs: list[NodeRun] = []
        for row in rows:
            completed_at = completion_of(records.get(str(row.id), {}), wait_keys)
            if completed_at is None:
                continue
            queued_at = covered.get(str(row.id))
            if queued_at is not None and queued_at >= completed_at:
                continue
            runs.append(
                NodeRun(
                    account_id=self.account_id,
                    fill_run_id=None,
                    node_id=str(self.node.id),
                    kind=WEBHOOK,
                    row_id=str(row.id),
                    list_id=list_id,
                    position=row.position,
                    status=NodeRunStatus.DEFERRED,
                    not_before=window,
                    queued_at=now,
                    last_state_change_at=now,
                )
            )
        if not runs:
            return 0
        NodeRun.objects.bulk_create(runs, ignore_conflicts=True)
        return len(runs)

    def _newest_run_at(self, row_ids: Sequence[str]) -> dict[str, datetime]:
        """row id -> when this node's newest run for it was queued, for
        the rows that have one (served by `node_run_webhook_cell_idx`)."""
        newest = (
            NodeRun.objects.filter(
                account_id=self.account_id, kind=WEBHOOK, node_id=str(self.node.id), row_id__in=list(row_ids)
            )
            .order_by("row_id", "-id")
            .distinct("row_id")
            .values_list("row_id", "queued_at")
        )
        return {row_id: queued_at for row_id, queued_at in newest if queued_at is not None}


register(WebhookProcessor)

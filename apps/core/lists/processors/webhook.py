"""The webhook kind's processor: a row is owed a run once every column
its wait barrier waits on is done, and the run is born DEFERRED at the
next boundary of the node's cadence, so every row completing inside
one window rides one digest. The barrier is the wait node ahead of the
webhook on its path; this is the ONE place that resolves it to columns
(the flush, the backfill, the advance, and the column's config read all
ask here)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import ClassVar

from ..constants import NodeRunStatus
from ..models import List, ListRow, NodeRun
from ..nodes.registry import WEBHOOK
from ..nodes.webhook import Webhook
from ..services.digest_payload import completion_of
from ..services.webhook_paths import wait_keys_for
from ..services.workflows import NodeNotFound, WorkflowService, config_as
from .base import CellRecords, NodeProcessor
from .factory import register


def next_window(now: datetime, interval_seconds: int) -> datetime:
    """The next boundary of an interval on the epoch clock, strictly
    after `now`. Second resolution; a `now` exactly on a boundary
    yields the boundary after it."""
    boundary = (int(now.timestamp()) // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


class WebhookProcessor(NodeProcessor):
    KIND: ClassVar[str] = WEBHOOK

    def needs(self, target_list: List) -> list[str]:
        """The columns the barrier ahead of this node waits on, in sheet
        order: its inbound paths resolved to the agent nodes ending
        them, then to the columns those nodes fill on this sheet. A
        path that no longer resolves drops out; a barrier that is gone,
        or resolves to nothing, needs nothing, and the caller offers no
        rows."""
        workflows = WorkflowService(account_id=self.account_id)
        try:
            wait = workflows.wait_ahead_of(self.node)
        except NodeNotFound:
            return []
        return wait_keys_for(wait.inbound_path_ids, columns=target_list.columns, node_by_path=self._node_by_path(wait))

    def materialize(
        self, target_list: List, rows: Sequence[ListRow], records: Mapping[str, CellRecords], *, now: datetime
    ) -> list[NodeRun]:
        wait_keys = self.needs(target_list)
        if not wait_keys:
            return []
        webhook = config_as(self.node, Webhook)
        window = next_window(now, webhook.interval_seconds)
        runs: list[NodeRun] = []
        for row in rows:
            if completion_of(records.get(str(row.id), {}), wait_keys) is None:
                continue
            runs.append(
                NodeRun(
                    account_id=self.account_id,
                    fill_run_id=None,
                    node_id=str(self.node.id),
                    kind=WEBHOOK,
                    row_id=str(row.id),
                    list_id=str(target_list.id),
                    position=row.position,
                    status=NodeRunStatus.DEFERRED,
                    not_before=window,
                    queued_at=now,
                    last_state_change_at=now,
                )
            )
        return runs

    def _node_by_path(self, wait) -> dict[str, str]:
        agent_nodes = WorkflowService(account_id=self.account_id).nodes_ending(wait.inbound_path_ids)
        return {node.path_id: str(node.id) for node in agent_nodes}


register(WebhookProcessor)

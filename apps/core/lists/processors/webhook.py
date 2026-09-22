"""The webhook kind's processor: a row is owed a run once every column
its wait barrier waits on is done, and the run is born DEFERRED at the
next boundary of the node's cadence, so every row completing inside
one window rides one digest. The barrier is the wait node ahead of the
webhook on its path; this is the ONE place that resolves it to columns
(the flush, the backfill, the advance, and the column's config read all
ask here). The walk scope is irrelevant to this kind: a webhook judges
every pass the same way.

Its runs execute per NODE, not per run: `_process_batch` is one tick's
work for one node. Gate (a paused column or a disabled destination
skips without claiming), claim the due runs in one CAS (two ticks
overlapping split a node's rows rather than both sending them),
re-check completion off the cells (the cells are the truth; a run whose
row is no longer complete is put back with its attempt handed back),
build the digest in sheet order, deliver OUTSIDE any transaction, then
settle: sent, or parked to the next window on a transient failure
until the attempt cap, or failed at once on a rejection. Every attempt
is its own delivery row and webhook-id; a receiver dedups on the item's
event_id, which is derived from the row's completion and so survives a
retry."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

from webhooks.constants import DeliveryStatus
from webhooks.models import WebhookDestination
from webhooks.services import Sent, WebhookDestinationService

from ..constants import NODE_RUN_ATTEMPTS, WEBHOOK_FLUSH_BATCH, NodeRunStatus, WebhookRunOutcome
from ..models import List, ListRow, NodeRun
from ..nodes.registry import WEBHOOK
from ..nodes.webhook import Webhook
from ..services.cell_states import CellStateService
from ..services.digest_payload import build_digest_data, build_digest_item, completion_of
from ..services.node_runs import NodeRunFlow
from ..services.webhook_paths import wait_keys_for
from ..services.webhook_runs import WebhookRunResult
from ..services.workflows import NodeNotFound, WorkflowService, config_as
from .base import BatchTally, NodeProcessor
from .factory import register

# What a run's result says when its column or destination is gone
# before it sent: terminal, because the fact never changes.
COLUMN_REMOVED = "The Send webhook column was removed before this row sent."
DESTINATION_REMOVED = "The destination was removed before this row sent."


def next_window(now: datetime, interval_seconds: int) -> datetime:
    """The next boundary of an interval on the epoch clock, strictly
    after `now`. Second resolution; a `now` exactly on a boundary
    yields the boundary after it."""
    boundary = (int(now.timestamp()) // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


def fail_due(flow: NodeRunFlow, node_id: str, *, now: datetime, error: str) -> int:
    """Close a node's due runs as failed: its column or destination is
    gone, and waiting forever would be a lie. Nothing is sent. A module
    function because the gone-COLUMN case has no node to build a
    processor from."""
    claimed = flow.claim_due_batch(node_id, now=now, limit=WEBHOOK_FLUSH_BATCH)
    result = WebhookRunResult(outcome=WebhookRunOutcome.FAILED, error=error)
    return flow.settle_many([str(run.id) for run in claimed], result.model_dump(), status=NodeRunStatus.DONE)


class WebhookProcessor(NodeProcessor):
    KIND: ClassVar[str] = WEBHOOK

    def _process_batch(self, *, flow: NodeRunFlow, now: datetime) -> BatchTally:
        tally = BatchTally()
        node_id = str(self.node.id)
        webhook = config_as(self.node, Webhook)
        if not webhook.enabled:
            tally.skipped += 1
            return tally
        destination = WebhookDestination.objects.filter(id=webhook.destination_id, account_id=self.account_id).first()
        if destination is None:
            tally.failed += fail_due(flow, node_id, now=now, error=DESTINATION_REMOVED)
            return tally
        if not destination.enabled:
            tally.skipped += 1
            return tally

        claimed = flow.claim_due_batch(node_id, now=now, limit=WEBHOOK_FLUSH_BATCH)
        if not claimed:
            return tally
        claimed_ids = [str(run.id) for run in claimed]
        window = next_window(now, webhook.interval_seconds)
        list_id = claimed[0].list_id
        target_list = List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            flow.settle_many(claimed_ids, {}, status=NodeRunStatus.LIST_MISSING)
            return tally
        wait_keys = self.wait_keys(target_list)
        if not wait_keys:
            # The wait resolves to no column (its agent's columns left the
            # sheet): nothing to judge completion against, so nothing is
            # sent and no attempt is spent. The column edit that mends
            # the wait backfills again.
            tally.parked += flow.park_batch(claimed_ids, not_before=window, restore_attempt=True)
            return tally

        rows = {
            str(row.id): row for row in ListRow.objects.filter(id__in=[run.row_id for run in claimed], list_id=list_id)
        }
        missing = [str(run.id) for run in claimed if run.row_id not in rows]
        if missing:
            flow.settle_many(missing, {}, status=NodeRunStatus.ROW_MISSING)
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=self.account_id)
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=list(rows), column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)

        by_key = {column.key: column for column in target_list.columns}
        payload_keys = [key for key in webhook.payload_keys if key in by_key]
        sendable: list[NodeRun] = []
        incomplete: list[str] = []
        items = []
        # The digest reads top to bottom of the sheet AS IT IS: by the
        # rows' live ranks, not the rank stamped on each run when it was
        # queued (a row moved since would otherwise report in its old
        # place). The rows are loaded either way, so the sort is free.
        present = [run for run in claimed if run.row_id in rows]
        for run in sorted(present, key=lambda run: (rows[run.row_id].rank, run.row_id)):
            row = rows[run.row_id]
            completed_at = completion_of(records[run.row_id], wait_keys)
            if completed_at is None:
                # A refill re-opened a waited-on cell since the advance:
                # the cells are the truth, so the run waits for the next
                # window with its attempt handed back.
                incomplete.append(str(run.id))
                continue
            sendable.append(run)
            items.append(
                build_digest_item(
                    scope=node_id,
                    row_id=run.row_id,
                    cells={key: str(row.data.get(key) or "") for key in payload_keys},
                    states={key: state for key, (state, _updated_at) in records[run.row_id].items()},
                    completed_at=completed_at,
                    sent_at=now,
                    test=False,
                )
            )
        if incomplete:
            tally.parked += flow.park_batch(incomplete, not_before=window, restore_attempt=True)
        if not items:
            return tally

        data = build_digest_data(target_list, waited_on=wait_keys, items=items)
        # OUTSIDE any transaction: the POST is the slow, external part,
        # and nothing above holds one open.
        destinations = WebhookDestinationService(account_id=self.account_id, user_id=destination.user_id)
        sent = destinations.deliver(destination, test=False, data=data)
        self._settle(flow, sendable, sent, window=window, tally=tally)
        return tally

    def _settle(
        self, flow: NodeRunFlow, runs: list[NodeRun], sent: Sent, *, window: datetime, tally: BatchTally
    ) -> None:
        delivery = sent.delivery
        ids = [str(run.id) for run in runs]
        if delivery.status == DeliveryStatus.OK:
            result = WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id=str(delivery.id))
            tally.settled += flow.settle_many(ids, result.model_dump(), status=NodeRunStatus.DONE)
            # A run the reclaim took back mid-flight is re-offered too:
            # the advance is idempotent, and its row did complete.
            tally.settled_rows.extend((run.list_id, run.row_id) for run in runs if run.row_id)
            return
        failed = WebhookRunResult(outcome=WebhookRunOutcome.FAILED, delivery_id=str(delivery.id), error=delivery.error)
        if delivery.status != DeliveryStatus.TRANSIENT:
            # Rejected or blocked: a retry buys the same answer.
            tally.failed += flow.settle_many(ids, failed.model_dump(), status=NodeRunStatus.DONE)
            return
        # Transient: the attempt the claim stamped counts; past the cap
        # the run fails, the rest wait for the next window.
        exhausted = [str(run.id) for run in runs if run.attempts > NODE_RUN_ATTEMPTS]
        retrying = [str(run.id) for run in runs if run.attempts <= NODE_RUN_ATTEMPTS]
        tally.failed += flow.settle_many(exhausted, failed.model_dump(), status=NodeRunStatus.DONE)
        parked = WebhookRunResult(
            outcome=WebhookRunOutcome.RETRYING, delivery_id=str(delivery.id), error=delivery.error
        )
        tally.parked += flow.park_batch(retrying, not_before=window, result=parked.model_dump())

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

    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime, limit: int = 0) -> int:
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
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=row_ids, column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)
        covered = self._newest_run_at(row_ids)
        webhook = config_as(self.node, Webhook)
        window = next_window(now, webhook.interval_seconds)
        runs: list[NodeRun] = []
        for row in rows:
            if limit and len(runs) == limit:
                break
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
                    rank=row.rank,
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
        the rows that have one (served by `node_run_cell_idx`)."""
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

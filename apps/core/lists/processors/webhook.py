"""The webhook kind's processor: a row is owed a run once every column
its wait barrier waits on is done, and the run is born DEFERRED at the
next boundary of the node's cadence, so every row completing inside
one window rides one digest. The barrier is the wait node ahead of the
webhook on its path; this is the ONE place that resolves it to columns
(the flush, the backfill, the advance, and the column's config read all
ask here). What a send made of each row lands on the cell ledger like
any other column's outcome (SENT, FAILED), so the rows page, the
counts and a barrier behind this column read one ledger. The walk
scope is irrelevant to this kind: a webhook judges every pass the same
way.

Its runs execute per NODE, not per run: `_process_batch` is one tick's
work for one node, over the due runs the flush claimed. The same three
steps as the agent kind, at batch grain: build the lane (resolve what
the send needs, or end the batch: a paused column or a disabled
destination parks it back with the attempt handed back, a gone
destination fails it, and completion is re-checked off the cells, the
truth, so a run whose row is no longer complete is put back), deliver
ONE digest outside any transaction, settle by what came back."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar, NamedTuple

from django.db import transaction

from openbower_schema.lists import WebhookColumn
from webhooks.constants import DeliveryStatus
from webhooks.models import WebhookDestination
from webhooks.services import Sent, WebhookDestinationService

from ..cells import CellWrite, LandingContext, RowLanding, WebhookWrite
from ..constants import NODE_RUN_ATTEMPTS, CellSource, NodeRunStatus, StoredCellState, WebhookRunOutcome
from ..models import List, ListRow, NodeRun
from ..nodes.registry import WEBHOOK
from ..nodes.webhook import Webhook
from ..services.cell_states import CellStateService
from ..services.digest_payload import build_digest_data, build_digest_item, completion_of
from ..services.lists import ListService
from ..services.node_runs import NodeRunFlow
from ..services.webhook_paths import wait_keys_for
from ..services.webhook_runs import WebhookRunResult
from ..services.workflows import NodeNotFound, WorkflowService, config_as
from .base import BatchTally, FillScope, NodeProcessor
from .factory import register

# What a run's result says when its column or destination is gone
# before it sent: terminal, because the fact never changes.
COLUMN_REMOVED = "The Send webhook column was removed before this row sent."
DESTINATION_REMOVED = "The destination was removed before this row sent."
# What a run's result says when preparing the send raised every time it
# was tried: terminal at the attempt cap, because a crash that repeats
# on every claim is not waiting for anything.
SEND_UNPREPARABLE = "This row could not be sent: preparing it failed on every attempt."


def next_window(now: datetime, interval_seconds: int) -> datetime:
    """The next boundary of an interval on the epoch clock, strictly
    after `now`. Second resolution; a `now` exactly on a boundary
    yields the boundary after it."""
    boundary = (int(now.timestamp()) // interval_seconds + 1) * interval_seconds
    return datetime.fromtimestamp(boundary, tz=UTC)


def fail_claimed(flow: NodeRunFlow, tasks: Sequence[NodeRun], *, error: str) -> int:
    """Close claimed runs as failed: their column or destination is
    gone, and waiting forever would be a lie. Nothing is sent. A module
    function because the gone-COLUMN case has no node to build a
    processor from."""
    result = WebhookRunResult(outcome=WebhookRunOutcome.FAILED, error=error)
    return flow.settle_many([str(run.id) for run in tasks], result.model_dump(), status=NodeRunStatus.DONE)


class _BatchEnded(Exception):
    """Resolution settled or parked the whole batch before it could send
    (nothing to send for, nothing sendable): raised as its last act,
    carrying the tally of what it did, so resolution hands back one
    shape and touches nothing it was handed."""

    def __init__(self, tally: BatchTally) -> None:
        super().__init__(tally)
        self.tally = tally


class _SendableBatch(NamedTuple):
    """One node's claimed batch, resolved and ready to send: the
    destination, the digest's inputs (the sheet, the column this node
    is on the sheet, the barrier's columns, the payload's), the window
    a park goes to, and the sendable runs in sheet order with their
    rows, cell records and completion times."""

    destination: WebhookDestination
    target_list: List
    column_key: str
    wait_keys: list[str]
    payload_keys: list[str]
    window: datetime
    sendable: list[NodeRun]
    rows: dict[str, ListRow]
    records: dict[str, dict[str, tuple[str, datetime]]]
    completed_at: dict[str, datetime]
    # The claimed runs whose row is no longer complete (the wait set
    # gained a column since the advance; the cells are the truth):
    # resolution decides, the batch method parks them back.
    incomplete: list[str]


class _LandResult(NamedTuple):
    """What `_land` did: `count` is how many runs it finished (moved to
    DONE by its CAS, whichever outcome it recorded on their cells; the
    caller files them as sent or failed), and `landed` the runs whose row
    was still there to land on, which is what the advance reads (a run
    whose row was deleted during the send retires ROW_MISSING and is in
    neither)."""

    count: int
    landed: list[NodeRun]


class WebhookProcessor(NodeProcessor):
    KIND: ClassVar[str] = WEBHOOK

    def _process_batch(self, tasks: Sequence[NodeRun], *, flow: NodeRunFlow, now: datetime) -> BatchTally:
        try:
            batch = self._resolve(tasks, flow=flow, now=now)
        except _BatchEnded as ended:
            return ended.tally
        parked = flow.park_batch(batch.incomplete, not_before=batch.window, restore_attempt=True)
        held = BatchTally(parked=parked)
        if not batch.sendable:
            return held
        sent = self._deliver(batch, now=now)
        landed = self._settle(flow, batch, sent)
        return held + landed

    def _resolve(self, tasks: Sequence[NodeRun], *, flow: NodeRunFlow, now: datetime) -> _SendableBatch:
        """Resolve what the batch needs or end it: a paused column or a
        disabled destination hands the batch back untouched (its window
        stays, the attempt is handed back, the next tick finds it due
        again; a skip counts a node); a gone destination, or a column
        gone from the sheet, fails it; a gone list retires it; a
        barrier resolving to no column parks it (the column edit that
        mends the wait backfills again); a run whose row is gone
        retires. The rest are partitioned, sendable or incomplete, and
        handed back: this decides, it does not act on them."""
        task_ids = [str(run.id) for run in tasks]
        webhook = config_as(self.node, Webhook)
        window = next_window(now, webhook.interval_seconds)
        if not webhook.enabled:
            flow.release_batch(task_ids)
            raise _BatchEnded(BatchTally(skipped=1))
        destination = WebhookDestination.objects.filter(id=webhook.destination_id, account_id=self.account_id).first()
        if destination is None:
            raise _BatchEnded(BatchTally(failed=fail_claimed(flow, tasks, error=DESTINATION_REMOVED)))
        if not destination.enabled:
            flow.release_batch(task_ids)
            raise _BatchEnded(BatchTally(skipped=1))
        list_id = tasks[0].list_id
        target_list = List.objects.filter(id=list_id, account_id=self.account_id).first()
        if target_list is None:
            flow.settle_many(task_ids, {}, status=NodeRunStatus.LIST_MISSING)
            raise _BatchEnded(BatchTally())
        own_key = next(
            (c.key for c in target_list.columns if isinstance(c, WebhookColumn) and c.node_id == str(self.node.id)), ""
        )
        if not own_key:
            # The column is gone from the sheet while its node lingers:
            # nothing to land the outcome on, and nothing to send for.
            raise _BatchEnded(BatchTally(failed=fail_claimed(flow, tasks, error=COLUMN_REMOVED)))
        wait_keys = self.wait_keys(target_list)
        if not wait_keys:
            raise _BatchEnded(BatchTally(parked=flow.park_batch(task_ids, not_before=window, restore_attempt=True)))
        rows = {
            str(row.id): row for row in ListRow.objects.filter(id__in=[run.row_id for run in tasks], list_id=list_id)
        }
        missing = [str(run.id) for run in tasks if run.row_id not in rows]
        if missing:
            flow.settle_many(missing, {}, status=NodeRunStatus.ROW_MISSING)
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=self.account_id)
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=list(rows), column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)
        # Sheet order AS IT IS: by the rows' live ranks, not the rank
        # stamped on each run when it was queued (a row moved since would
        # otherwise report in its old place).
        present = sorted(
            (run for run in tasks if run.row_id in rows), key=lambda run: (rows[run.row_id].rank, run.row_id)
        )
        sendable: list[NodeRun] = []
        completed_at: dict[str, datetime] = {}
        incomplete: list[str] = []
        for run in present:
            when = completion_of(records[run.row_id], wait_keys)
            if when is None:
                incomplete.append(str(run.id))
                continue
            sendable.append(run)
            completed_at[run.row_id] = when
        by_key = {column.key: column for column in target_list.columns}
        return _SendableBatch(
            destination=destination,
            target_list=target_list,
            column_key=own_key,
            wait_keys=wait_keys,
            payload_keys=[key for key in webhook.payload_keys if key in by_key],
            window=window,
            sendable=sendable,
            rows=rows,
            records=records,
            completed_at=completed_at,
            incomplete=incomplete,
        )

    def _deliver(self, batch: _SendableBatch, *, now: datetime) -> Sent:
        """ONE digest of the sendable rows, delivered OUTSIDE any
        transaction: the POST is the slow, external part, and nothing
        here holds one open. Every attempt is its own delivery row and
        webhook-id; a receiver dedups on the item's event_id, derived
        from the row's completion, which survives a retry."""
        node_id = str(self.node.id)
        items = [
            build_digest_item(
                scope=node_id,
                row_id=run.row_id,
                cells={key: str(batch.rows[run.row_id].data.get(key) or "") for key in batch.payload_keys},
                states={key: state for key, (state, _updated_at) in batch.records[run.row_id].items()},
                completed_at=batch.completed_at[run.row_id],
                sent_at=now,
                test=False,
            )
            for run in batch.sendable
        ]
        data = build_digest_data(batch.target_list, waited_on=batch.wait_keys, items=items)
        destinations = WebhookDestinationService(account_id=self.account_id, user_id=batch.destination.user_id)
        return destinations.deliver(batch.destination, test=False, data=data)

    def _settle(self, flow: NodeRunFlow, batch: _SendableBatch, sent: Sent) -> BatchTally:
        """Land what came back and say what happened: SENT on every
        row's cell (the column's record on the ledger, then the runs
        closed, in the landing's lock order), or parked to the next
        window on a transient failure until the attempt cap, or FAILED
        at once on a rejection. A retry in flight records nothing: the
        open run IS the cell's pending."""
        delivery = sent.delivery
        runs = batch.sendable
        if delivery.status == DeliveryStatus.OK:
            sent_result = WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id=str(delivery.id))
            # A run the reclaim took back mid-flight is re-offered too:
            # the advance is idempotent, and its row did complete.
            result = self._land(flow, batch, runs, StoredCellState.SENT, sent_result)
            return BatchTally(
                settled=result.count,
                settled_rows=[(run.list_id, run.row_id) for run in result.landed if run.row_id],
            )
        failed_result = WebhookRunResult(
            outcome=WebhookRunOutcome.FAILED, delivery_id=str(delivery.id), error=delivery.error
        )
        if delivery.status != DeliveryStatus.TRANSIENT:
            # Rejected or blocked: a retry buys the same answer.
            result = self._land(flow, batch, runs, StoredCellState.FAILED, failed_result)
            return BatchTally(failed=result.count)
        # Transient: the attempt the claim stamped counts, and the cap is
        # judged AFTER the delivery, so a row's send is tried
        # NODE_RUN_ATTEMPTS + 1 times (5: the first plus 4 retries).
        # Past the cap the run fails; the rest wait for the next window.
        exhausted = [run for run in runs if run.attempts > NODE_RUN_ATTEMPTS]
        retrying = [str(run.id) for run in runs if run.attempts <= NODE_RUN_ATTEMPTS]
        parked_result = WebhookRunResult(
            outcome=WebhookRunOutcome.RETRYING, delivery_id=str(delivery.id), error=delivery.error
        )
        result = self._land(flow, batch, exhausted, StoredCellState.FAILED, failed_result)
        parked = flow.park_batch(retrying, not_before=batch.window, result=parked_result.model_dump())
        return BatchTally(failed=result.count, parked=parked)

    def on_run_landed(self, column_keys: Sequence[str], outcome: StoredCellState) -> list[CellWrite]:
        """The send's outcome on this node's one column: SENT, or FAILED.
        No value; the column holds none."""
        (key,) = column_keys
        return [WebhookWrite(key, outcome)]

    def _land(
        self,
        flow: NodeRunFlow,
        batch: _SendableBatch,
        runs: Sequence[NodeRun],
        state: StoredCellState,
        result: WebhookRunResult,
    ) -> _LandResult:
        """The batch's landing: the kind's write for every row through
        the list service's one landing (ONE ledger upsert for the
        batch), then the runs settled, one transaction, ListCellState
        before NodeRun (the order the deletes take). A run whose row was
        deleted during the send has no verdict: it retires ROW_MISSING,
        as a row gone before the send does, and the rest settle DONE
        with the result stored."""
        if not runs:
            return _LandResult(count=0, landed=[])
        writes = self.on_run_landed([batch.column_key], state)
        landings = [RowLanding(run.row_id, writes) for run in runs]
        ctx = LandingContext(list_id=str(batch.target_list.id), source=CellSource.NODE, fill_run_id=None)
        lists = ListService(account_id=self.account_id)
        with transaction.atomic():
            verdicts = lists.land_rows(ctx, landings)
            landed = [run for run in runs if run.row_id in verdicts]
            gone = [str(run.id) for run in runs if run.row_id not in verdicts]
            flow.settle_many(gone, {}, status=NodeRunStatus.ROW_MISSING)
            count = flow.settle_many([str(run.id) for run in landed], result.model_dump(), status=NodeRunStatus.DONE)
        return _LandResult(count=count, landed=landed)

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

    def _enqueue_runs(
        self, target_list: List, rows: Sequence[ListRow], *, scope: FillScope, now: datetime, limit: int = 0
    ) -> int:
        """`scope` is unread: a webhook judges every occasion by its
        barrier. A row is owed a run when it is complete for the barrier's
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

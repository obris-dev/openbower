"""The flush: one cron tick over every webhook node with runs due,
sending each node ONE digest of its due rows and settling the runs by
what came back. Global (not account-scoped): a trusted process, like
the reclaim, scoping every read by the node it found.

Per node the tick is: gate (a paused column or a disabled destination
skips without claiming), claim the due runs in one CAS (two ticks
overlapping split a node's rows rather than both sending them),
re-check completion off the cells (the cells are the truth; a run
whose row is no longer complete is put back with its attempt handed
back), build the digest in sheet order, deliver OUTSIDE any
transaction, then settle: sent, or parked to the next window on a
transient failure until the attempt cap, or failed at once on a
rejection. Every attempt is its own delivery row and webhook-id; a
receiver dedups on the item's event_id, which is derived from the
row's completion and so survives a retry.

Safe to miss (each run holds its own window in `not_before`) and safe
to double (the claim's status predicate). A node that raises is logged
and skipped, never allowed to stop every other account's tick; its
runs left PROCESSING return to DEFERRED through the reclaim.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from django.utils import timezone

from webhooks.constants import DeliveryStatus
from webhooks.models import WebhookDestination
from webhooks.services import Sent, WebhookDestinationService

from ..constants import NODE_RUN_ATTEMPTS, WEBHOOK_FLUSH_BATCH, NodeRunStatus, WebhookRunOutcome
from ..models import List, ListRow, Node, NodeRun
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from ..services.cell_states import CellStateService
from ..services.digest_payload import build_digest_data, build_digest_item, completion_of
from ..services.node_runs import NodeRunFlow
from ..services.webhook_runs import WebhookRunResult, next_window, wait_keys_of
from ..services.workflows import WorkflowService, config_as

logger = logging.getLogger(__name__)

# What a run's result says when its column or destination is gone
# before it sent: terminal, because the fact never changes.
COLUMN_REMOVED = "The Send webhook column was removed before this row sent."
DESTINATION_REMOVED = "The destination was removed before this row sent."


@dataclass
class FlushReport:
    """One tick's tallies, for the command's log line: runs, not
    digests (one digest carries many runs)."""

    nodes: int = 0
    sent: int = 0
    parked: int = 0
    failed: int = 0
    skipped: int = 0


class FlushWebhooksOperation:
    def __init__(self, *, worker_id: str) -> None:
        self.flow = NodeRunFlow(worker_id=worker_id)

    def run(self, *, now: datetime | None = None) -> FlushReport:
        now = now or timezone.now()
        report = FlushReport()
        # Materialized first: the claims below mutate what the pick reads.
        for node_id in list(self.flow.iter_due_webhook_nodes(now=now)):
            report.nodes += 1
            try:
                self._flush_node(node_id, now=now, report=report)
            except DatabaseError:
                # The connection is the tick's; nothing here recovers it.
                raise
            except Exception:
                # A crash inside one node (a config that no longer parses,
                # a bug) must not stop every other account's tick: log the
                # traceback and move on. Its claimed runs come back
                # DEFERRED through the reclaim.
                logger.exception("flush_webhooks: node %s failed; skipping it this tick", node_id)
        return report

    def _flush_node(self, node_id: str, *, now: datetime, report: FlushReport) -> None:
        node = Node.objects.filter(id=node_id).first()
        if node is None:
            report.failed += self._fail_due(node_id, now=now, error=COLUMN_REMOVED)
            return
        webhook = config_as(node, Webhook)
        if not webhook.enabled:
            report.skipped += 1
            return
        destination = WebhookDestination.objects.filter(id=webhook.destination_id, account_id=node.account_id).first()
        if destination is None:
            report.failed += self._fail_due(node_id, now=now, error=DESTINATION_REMOVED)
            return
        if not destination.enabled:
            report.skipped += 1
            return

        claimed = self.flow.claim_webhook_batch(node_id, now=now, limit=WEBHOOK_FLUSH_BATCH)
        if not claimed:
            return
        claimed_ids = [str(run.id) for run in claimed]
        window = next_window(now, webhook.interval_seconds)
        list_id = claimed[0].list_id
        target_list = List.objects.filter(id=list_id, account_id=node.account_id).first()
        if target_list is None:
            self.flow.settle_many(claimed_ids, {}, status=NodeRunStatus.LIST_MISSING)
            return
        wait_keys = self._wait_keys(node, target_list)
        if not wait_keys:
            # The wait resolves to no column (its agent's columns left the
            # sheet): nothing to judge completion against, so nothing is
            # sent and no attempt is spent. The column edit that mends
            # the wait backfills again.
            report.parked += self.flow.park_batch(claimed_ids, not_before=window, restore_attempt=True)
            return

        rows = {
            str(row.id): row for row in ListRow.objects.filter(id__in=[run.row_id for run in claimed], list_id=list_id)
        }
        missing = [str(run.id) for run in claimed if run.row_id not in rows]
        if missing:
            self.flow.settle_many(missing, {}, status=NodeRunStatus.ROW_MISSING)
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=node.account_id)
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=list(rows), column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)

        by_key = {column.key: column for column in target_list.columns}
        payload_keys = [key for key in webhook.payload_keys if key in by_key]
        sendable: list[NodeRun] = []
        incomplete: list[str] = []
        items = []
        # The claim's order is (position, id): the digest reads top to
        # bottom of the sheet.
        for run in claimed:
            row = rows.get(run.row_id)
            if row is None:
                continue
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
                    scope=str(node.id),
                    row=row,
                    cells={key: str(row.data.get(key) or "") for key in payload_keys},
                    states={key: state for key, (state, _updated_at) in records[run.row_id].items()},
                    completed_at=completed_at,
                    sent_at=now,
                    test=False,
                )
            )
        if incomplete:
            report.parked += self.flow.park_batch(incomplete, not_before=window, restore_attempt=True)
        if not items:
            return

        data = build_digest_data(target_list, waited_on=wait_keys, items=items)
        # OUTSIDE any transaction: the POST is the slow, external part,
        # and nothing above holds one open.
        destinations = WebhookDestinationService(account_id=node.account_id, user_id=destination.user_id)
        sent = destinations.deliver(destination, test=False, data=data)
        self._settle(sendable, sent, window=window, report=report)

    def _settle(self, runs: list[NodeRun], sent: Sent, *, window: datetime, report: FlushReport) -> None:
        delivery = sent.delivery
        ids = [str(run.id) for run in runs]
        if delivery.status == DeliveryStatus.OK:
            result = WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id=str(delivery.id))
            report.sent += self.flow.settle_many(ids, result.model_dump(), status=NodeRunStatus.DONE)
            return
        failed = WebhookRunResult(outcome=WebhookRunOutcome.FAILED, delivery_id=str(delivery.id), error=delivery.error)
        if delivery.status != DeliveryStatus.TRANSIENT:
            # Rejected or blocked: a retry buys the same answer.
            report.failed += self.flow.settle_many(ids, failed.model_dump(), status=NodeRunStatus.DONE)
            return
        # Transient: the attempt the claim stamped counts; past the cap
        # the run fails, the rest wait for the next window.
        exhausted = [str(run.id) for run in runs if run.attempts > NODE_RUN_ATTEMPTS]
        retrying = [str(run.id) for run in runs if run.attempts <= NODE_RUN_ATTEMPTS]
        report.failed += self.flow.settle_many(exhausted, failed.model_dump(), status=NodeRunStatus.DONE)
        parked = WebhookRunResult(
            outcome=WebhookRunOutcome.RETRYING, delivery_id=str(delivery.id), error=delivery.error
        )
        report.parked += self.flow.park_batch(retrying, not_before=window, result=parked.model_dump())

    def _fail_due(self, node_id: str, *, now: datetime, error: str) -> int:
        """Close a node's due runs as failed: its column or destination
        is gone, and waiting forever would be a lie. Nothing is sent."""
        claimed = self.flow.claim_webhook_batch(node_id, now=now, limit=WEBHOOK_FLUSH_BATCH)
        result = WebhookRunResult(outcome=WebhookRunOutcome.FAILED, error=error)
        return self.flow.settle_many([str(run.id) for run in claimed], result.model_dump(), status=NodeRunStatus.DONE)

    @staticmethod
    def _wait_keys(node: Node, target_list: List) -> list[str]:
        """The columns the node's wait node waits on, re-resolved now."""
        workflows = WorkflowService(account_id=node.account_id)
        path_nodes = workflows.nodes_on_path(node.path_id)
        if not path_nodes or path_nodes[0].kind != WaitUntil.KIND:
            return []
        wait = config_as(path_nodes[0], WaitUntil)
        return wait_keys_of(account_id=node.account_id, target_list=target_list, wait=wait)

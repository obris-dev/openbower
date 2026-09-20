"""The flush: one cron tick over every webhook node with runs due,
handing each node to its processor, which sends ONE digest of the
node's due rows and settles the runs by what came back. Global (not
account-scoped): a trusted process, like the reclaim, scoping every
read by the node it found.

Safe to miss (each run holds its own window in `not_before`) and safe
to double (the claim's status predicate). A node that raises is logged
and skipped, never allowed to stop every other account's tick; its
runs left PROCESSING return to DEFERRED through the reclaim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from django.utils import timezone

from ..models import Node
from ..processors import BatchTally, processor_for
from ..processors.webhook import COLUMN_REMOVED, fail_due
from ..services.node_runs import NodeRunFlow

logger = logging.getLogger(__name__)


@dataclass
class FlushReport:
    """One tick's tallies, for the command's log line: runs, not
    digests (one digest carries many runs)."""

    nodes: int = 0
    sent: int = 0
    parked: int = 0
    failed: int = 0
    skipped: int = 0

    def absorb(self, tally: BatchTally) -> None:
        self.sent += tally.sent
        self.parked += tally.parked
        self.failed += tally.failed
        self.skipped += tally.skipped


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
                node = Node.objects.filter(id=node_id).first()
                if node is None:
                    # No node to build a processor from: its runs close
                    # here, as failed.
                    report.failed += fail_due(self.flow, node_id, now=now, error=COLUMN_REMOVED)
                    continue
                report.absorb(
                    processor_for(account_id=node.account_id, node=node).process_batch(flow=self.flow, now=now)
                )
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

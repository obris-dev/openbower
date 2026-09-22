"""The flush: one cron tick over every node with a DEFERRED run due,
claiming the node's due runs in one CAS and handing the batch to its
processor's batch method, which settles them by what it did (the
webhook kind sends ONE digest of them). Knows no kind: a kind that defers is
one that overrides _process_batch. Global (not account-scoped): a
trusted process, like the reclaim, scoping every read by the node it
found.

Safe to miss (each run holds its own window in `not_before`) and safe
to double (the claim's status predicate). A node the kind cannot send
for right now (a paused column, a disabled destination) has its batch
handed back untouched, due again on the next tick. A node that raises is logged
and skipped, never allowed to stop every other account's tick; its
runs left PROCESSING return to DEFERRED through the reclaim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from django.utils import timezone

# The ONE kind-specific line: a node that is gone has no processor to
# ask, and its due runs still need a terminal shape their cell word can
# read. The webhook kind's is the only one today; a second deferring
# kind branches here on the runs' stored kind, as the reclaim does.
from ..constants import WEBHOOK_FLUSH_BATCH
from ..models import Node
from ..processors import BatchTally, processor_for
from ..processors.webhook import COLUMN_REMOVED, fail_claimed
from ..services.node_runs import NodeRunFlow

logger = logging.getLogger(__name__)


@dataclass
class FlushReport:
    """One tick's tallies, for the command's log line: runs, not
    batches (one batch carries many runs)."""

    nodes: int = 0
    settled: int = 0
    parked: int = 0
    failed: int = 0
    skipped: int = 0

    def absorb(self, tally: BatchTally) -> None:
        self.settled += tally.settled
        self.parked += tally.parked
        self.failed += tally.failed
        self.skipped += tally.skipped


class FlushDeferredOperation:
    def __init__(self, *, worker_id: str) -> None:
        self.flow = NodeRunFlow(worker_id=worker_id)

    def run(self, *, now: datetime | None = None) -> FlushReport:
        now = now or timezone.now()
        report = FlushReport()
        # Materialized first: the claims below mutate what the pick reads.
        for node_id in list(self.flow.iter_due_nodes(now=now)):
            report.nodes += 1
            try:
                # The dispatcher claims (one CAS; two ticks overlapping on
                # a node split its due rows), the kind executes.
                claimed = self.flow.claim_due_batch(node_id, now=now, limit=WEBHOOK_FLUSH_BATCH)
                if not claimed:
                    continue
                node = Node.objects.filter(id=node_id).first()
                if node is None:
                    # No node to build a processor from: its runs close
                    # here, as failed.
                    report.failed += fail_claimed(self.flow, claimed, error=COLUMN_REMOVED)
                    continue
                report.absorb(
                    processor_for(account_id=node.account_id, node=node).process_batch(claimed, flow=self.flow, now=now)
                )
            except DatabaseError:
                # The connection is the tick's; nothing here recovers it.
                raise
            except Exception:
                # A crash inside one node (a config that no longer parses,
                # a bug) must not stop every other account's tick: log the
                # traceback and move on. Its claimed runs come back
                # DEFERRED through the reclaim.
                logger.exception("flush_deferred: node %s failed; skipping it this tick", node_id)
        return report

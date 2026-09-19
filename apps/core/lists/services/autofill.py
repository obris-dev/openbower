"""Enqueue autofill work onto the unified task spine.

A pushed row is owed a fill of its BLANK AI columns. This queues that
as null-run NodeRuns (no Fill, so no consent run): one task per (row,
node), because a node's agent produces its whole column set in one
run. A node a push fully overrides (every column it fills already
carries a sent value) gets no task. The autofill worker claims them,
resolves each node's agent and column set live, and runs it.

Enqueue rides the caller's transaction (the ingest apply's), so a row
and its autofill work commit together or not at all: a pushed row can
never land visible with no work queued to fill it. Idempotent by the
autofill unique key (row, node) among null-run tasks, so a redelivery
that slips past the inbox cannot double-queue.
"""

from __future__ import annotations

from django.utils import timezone

from ..constants import NodeRunStatus
from ..models import List, NodeRun
from ..nodes.registry import COLUMN_AGENT
from .workflows import columns_by_node


def enqueue_rows(*, account_id: str, target_list: List, rows: list) -> int:
    """Queue autofill for freshly appended rows. No-op when the sheet
    has no AI columns or no rows arrived. Returns the number of tasks
    attempted; a re-enqueue of an already-queued (row, node) is a
    no-op under the idempotency key, so this can over-count a
    redelivery (the sole caller ignores it)."""
    node_columns = columns_by_node(target_list)
    if not node_columns or not rows:
        return 0
    # Born READY (not the QUEUED default): admission's shimmer counts any
    # non-terminal state, and the provisioner moves READY -> QUEUED when
    # it publishes. `last_state_change_at` is stamped at birth so the
    # reclaim scan and audit have a value from the start.
    now = timezone.now()
    tasks: list[NodeRun] = []
    for row in rows:
        for node_id, keys in node_columns.items():
            # Skip a fully overridden node: a push that fills every column
            # it owns leaves it no work (write-if-blank would keep those
            # values, so the run only buys a skip). If ANY is blank the
            # node still runs, and write-if-blank protects the ones sent.
            if all((row.data.get(key) or "").strip() for key in keys):
                continue
            tasks.append(
                NodeRun(
                    account_id=account_id,
                    fill_run_id=None,
                    node_id=node_id,
                    # An AiColumn binds a column_agent node by contract,
                    # so the kind needs no node read here.
                    kind=COLUMN_AGENT,
                    row_id=str(row.id),
                    list_id=str(target_list.id),
                    status=NodeRunStatus.READY,
                    last_state_change_at=now,
                )
            )
    if not tasks:
        return 0
    NodeRun.objects.bulk_create(tasks, ignore_conflicts=True)
    return len(tasks)

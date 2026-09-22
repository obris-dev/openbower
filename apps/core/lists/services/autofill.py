"""Enqueue autofill work onto the unified task spine.

A pushed row is owed a fill of its BLANK AI columns. This queues that
as null-run NodeRuns (no Fill, so no consent run): one task per (row,
node), because a node's agent produces its whole column set in one
run. Which rows a node still has work on is the agent processor's
judgement (mode PUSHED: a node the push fully overrides gets no task).
The autofill worker claims them, resolves each node's agent and column
set live, and runs it.

Enqueue rides the caller's transaction (the ingest apply's), so a row
and its autofill work commit together or not at all: a pushed row can
never land visible with no work queued to fill it. Idempotent by the
autofill unique key (row, node) among null-run tasks, so a redelivery
that slips past the inbox cannot double-queue.
"""

from __future__ import annotations

from django.utils import timezone

from ..models import List
from ..processors import WalkMode, WalkScope, processor_for
from .workflows import WorkflowService, columns_by_node


def enqueue_rows(*, account_id: str, target_list: List, rows: list) -> int:
    """Queue autofill for freshly appended rows: each agent node the
    sheet's columns bind to is offered the rows, and its processor
    (mode PUSHED) queues a run for every row it still has work on. No-op
    when the sheet has no AI columns or no rows arrived. Returns the
    runs queued; a re-enqueue of an already-queued (row, node) is a
    no-op under the open-run key, so this can under-count a redelivery
    (the sole caller ignores it)."""
    keys_by_node = columns_by_node(target_list)
    if not keys_by_node or not rows:
        return 0
    now = timezone.now()
    queued = 0
    for node in WorkflowService(account_id=account_id).nodes_by_id(list(keys_by_node)):
        # The columns the judgement looks at ride the scope: the starter
        # decides them, the processor never reads the sheet for them.
        scope = WalkScope(mode=WalkMode.PUSHED, column_keys=keys_by_node[str(node.id)])
        queued += processor_for(account_id=account_id, node=node, scope=scope).enqueue_runs(target_list, rows, now=now)
    return queued

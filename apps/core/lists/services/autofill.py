"""Enqueue autofill work onto the unified task spine.

A pushed row is owed a fill of its AI columns. This queues that as
null-run FillTasks (no Fill, so no consent run): one task per (row,
agent), because an agent produces its whole column set in one run. The
autofill worker claims them, resolves each agent's config and column
set live, and runs it.

Enqueue rides the caller's transaction (the ingest apply's), so a row
and its autofill work commit together or not at all: a pushed row can
never land visible with no work queued to fill it. Idempotent by the
autofill unique key (row, agent) among null-run tasks, so a redelivery
that slips past the inbox cannot double-queue.
"""

from __future__ import annotations

from ..models import FillTask, List


def _fill_agent_ids(target: List) -> set[str]:
    """The distinct agents the sheet's AI columns run. Each is one
    autofill task per row (one run fills that agent's whole column
    set)."""
    return {
        column["fill"]["agent_id"] for column in target.columns if column.get("fill") and column["fill"].get("agent_id")
    }


def enqueue_rows(*, account_id: str, target: List, rows: list) -> int:
    """Queue autofill for freshly appended rows. No-op when the sheet
    has no AI columns or no rows arrived. Returns the number of tasks
    attempted; a re-enqueue of an already-queued (row, agent) is a
    no-op under the idempotency key, so this can over-count a
    redelivery (the sole caller ignores it)."""
    agent_ids = _fill_agent_ids(target)
    if not agent_ids or not rows:
        return 0
    tasks = [
        FillTask(account_id=account_id, fill_run_id=None, agent_id=agent_id, row_id=str(row.id))
        for row in rows
        for agent_id in agent_ids
    ]
    FillTask.objects.bulk_create(tasks, ignore_conflicts=True)
    return len(tasks)

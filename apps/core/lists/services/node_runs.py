"""The node-run STATE MACHINE: READY -> QUEUED -> PROCESSING -> terminal,
the transitions the provisioner + shared consumer drive.

Every transition is a CAS `UPDATE` that also sets the state's own stamp
and `last_state_change_at` (the unified cursor the reclaim scan reads), so the
DB row is the single source of truth: no lease renewal (a bounded
run_cell + a stale `last_state_change_at` mean the owner is dead, not
slow) and no in-memory task state.

Both fill lanes ride this one flow: the autofill firehose (null-run
tasks) and the manual, fill-backed tasks. The provisioners pick READY
tasks (globally for autofill, per-fill for manual) and the shared
consumer claims/settles/parks them. A DEFERRED run (a webhook run,
waiting for its window) belongs to its kind's own processor and never
enters these lanes: the picks and claims gate on READY|QUEUED, and the
reclaim returns a stale one to DEFERRED rather than READY.

Not account-scoped: the workers are trusted processes; user-facing reads
live in fills.py.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator, Sequence

from django.db import models
from django.utils import timezone

from ..constants import NODE_RUN_ATTEMPTS, NodeRunStatus
from ..models import NodeRun
from ..nodes.registry import COLUMN_AGENT, WEBHOOK

# The owner tolerates death, not slowness: a task PROCESSING longer than
# this was abandoned by a dead consumer (run_cell is timeout-bounded, so
# a live run always reaches a terminal state first). The reclaim scan reclaims
# past it. Sized to the worst-case single run the worker stop-grace
# already encodes.
PROCESSING_STALE_SECONDS = 35 * 60


class NodeRunFlow:
    """State transitions for one worker/consumer (its id stamps the
    PROCESSING claim, so only the owner settles what it claimed)."""

    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id

    @staticmethod
    def iter_ready(*, limit: int) -> Iterator[NodeRun]:
        """The autofill provisioner's pick, LAZILY (single-pass): up to
        `limit` READY null-run (autofill) tasks that are due (a parked
        retry backs off in `not_before`), in (list_id, position, id)
        order. Grouping by list first is breadth-first across lists and
        is what `node_run_autofill_idx` (partial on the null-run rows)
        serves so the LIMIT stops early, and the seam a sharded pick
        narrows to its lists. Streamed via .iterator() so a growing queue
        never materializes as one list. The kind filter names the lane
        beside the status gate (a webhook run is never READY, but the
        pick that defines the agent lane says so itself)."""
        now = timezone.now()
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        qs = NodeRun.objects.filter(due, status=NodeRunStatus.READY, fill_run_id__isnull=True, kind=COLUMN_AGENT)
        yield from qs.defer("result").order_by("list_id", "position", "id")[:limit].iterator()

    @staticmethod
    def iter_ready_for_fill(fill_run_id: str, *, limit: int) -> Iterator[NodeRun]:
        """The manual provisioner's per-fill pick, LAZILY (single-pass):
        up to `limit` of this fill's READY, due tasks in SHEET ORDER
        (position) so the fill marches top to bottom down the sheet the
        user is watching. A flat `limit` per fill is the fairness point:
        a wide fill cannot flood the bus ahead of a smaller one beside
        it. `node_run_fill_idx` (fill_run_id equality, then position)
        serves it so the LIMIT stops early. Streamed via .iterator()."""
        now = timezone.now()
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        yield from (
            NodeRun.objects.filter(due, fill_run_id=fill_run_id, status=NodeRunStatus.READY)
            .defer("result")
            .order_by("position", "id")[:limit]
            .iterator()
        )

    @staticmethod
    def mark_queued(task: NodeRun) -> bool:
        """READY -> QUEUED, after the provisioner has published it. The CAS
        matches status READY AND the `last_state_change_at` token the page
        was read with, so a task that moved during the publish is left
        alone: the consumer accepts READY too, so it can claim
        (-> PROCESSING, token bumped) and even PARK back to READY (token
        bumped again) before this mark runs. Status alone cannot tell that
        re-READY task from a never-published one; marking it QUEUED would
        strand it, because the park already consumed its message and
        nothing re-drives QUEUED (reclaim touches only PROCESSING, the
        provisioner re-picks only READY)."""
        now = timezone.now()
        return (
            NodeRun.objects.filter(
                id=task.id,
                status=NodeRunStatus.READY,
                last_state_change_at=task.last_state_change_at,
            ).update(
                status=NodeRunStatus.QUEUED,
                queued_at=now,
                last_state_change_at=now,
            )
            == 1
        )

    def claim(self, task_id: str) -> NodeRun | None:
        """(READY | QUEUED) -> PROCESSING for this consumer. Accepts READY
        too, so a message that outran the provisioner's `mark_queued` (a
        crash between publish and mark) still runs. Stamps the owner
        (`leased_by`) so only this consumer settles it, and increments
        `attempts` HERE so a task that kills its consumer still exhausts.
        Returns the claimed task, or None when the CAS lost (a duplicate
        delivery, or another consumer / the reclaim scan got there first) and
        the message should be dropped."""
        now = timezone.now()
        claimed = NodeRun.objects.filter(
            id=task_id,
            status__in=(NodeRunStatus.READY, NodeRunStatus.QUEUED),
        ).update(
            status=NodeRunStatus.PROCESSING,
            processing_at=now,
            last_state_change_at=now,
            leased_by=self.worker_id,
            attempts=models.F("attempts") + 1,
        )
        if claimed != 1:
            return None
        return NodeRun.objects.get(id=task_id)

    @staticmethod
    def exhausted(task: NodeRun) -> bool:
        """Whether this claim is one too many, read AFTER the claim
        stamped its attempt (so a crash mid-run still counts)."""
        return task.attempts > NODE_RUN_ATTEMPTS

    def settle(self, task_id: str, result: dict, *, status: NodeRunStatus) -> bool:
        """PROCESSING -> a terminal state, CAS on the owner stamp so a
        reclaimed task's original consumer misses silently. `result` is
        positional so a `partial(settle, task_id, status=...)` matches
        landing.py's `close(result)` contract (the one shared by every
        terminal writer). Returns whether the close landed; landing.py
        runs this inside its own transaction and rolls the sheet + cell
        writes back on a miss."""
        now = timezone.now()
        return (
            NodeRun.objects.filter(
                id=task_id,
                status=NodeRunStatus.PROCESSING,
                leased_by=self.worker_id,
            ).update(
                status=status,
                result=result,
                settled_at=now,
                last_state_change_at=now,
            )
            == 1
        )

    def park(self, task_id: str, *, backoff_seconds: int, result: dict) -> bool:
        """PROCESSING -> READY with a backoff (a 429 or timeout): the task
        re-enters the queue once `not_before` passes, diagnosing nothing
        on the sheet. Owner CAS. Exhaustion is not decided here; the next
        claim stamps the attempt and the consumer gives up at the cap."""
        now = timezone.now()
        return (
            NodeRun.objects.filter(
                id=task_id,
                status=NodeRunStatus.PROCESSING,
                leased_by=self.worker_id,
            ).update(
                status=NodeRunStatus.READY,
                not_before=now + datetime.timedelta(seconds=backoff_seconds),
                parked=True,
                last_state_change_at=now,
                leased_by="",
                result=result,
            )
            == 1
        )

    # The deferred lane: a webhook node's runs, claimed in a batch by the
    # flush at their window and settled or parked together.

    @staticmethod
    def iter_due_webhook_nodes(*, now: datetime.datetime) -> Iterator[str]:
        """The webhook nodes with a DEFERRED run due at or before `now`,
        each once, LAZILY: the flush materializes the list before it
        claims, since claiming mutates what this reads."""
        due = NodeRun.objects.filter(kind=WEBHOOK, status=NodeRunStatus.DEFERRED, not_before__lte=now)
        yield from due.values_list("node_id", flat=True).distinct().iterator()

    def claim_webhook_batch(self, node_id: str, *, now: datetime.datetime, limit: int) -> list[NodeRun]:
        """DEFERRED -> PROCESSING for up to `limit` of one webhook node's
        due runs, in sheet order, stamping this worker and the attempt.
        The UPDATE matches status DEFERRED again, so two flush ticks
        overlapping on one node split its due rows between them instead
        of both sending the same digest; what this worker won is
        re-read by its stamp. Returns the claimed runs in (position, id)
        order, empty when another tick got there first."""
        due = NodeRun.objects.filter(kind=WEBHOOK, node_id=node_id, status=NodeRunStatus.DEFERRED, not_before__lte=now)
        ids = list(due.order_by("position", "id").values_list("id", flat=True)[:limit])
        if not ids:
            return []
        NodeRun.objects.filter(id__in=ids, status=NodeRunStatus.DEFERRED).update(
            status=NodeRunStatus.PROCESSING,
            processing_at=now,
            last_state_change_at=now,
            leased_by=self.worker_id,
            attempts=models.F("attempts") + 1,
        )
        won = NodeRun.objects.filter(id__in=ids, status=NodeRunStatus.PROCESSING, leased_by=self.worker_id)
        return list(won.order_by("position", "id"))

    def settle_many(self, task_ids: Sequence[str], result: dict, *, status: NodeRunStatus) -> int:
        """PROCESSING -> a terminal state for a batch this worker holds,
        one statement, owner CAS: a run the reclaim took back mid-flight
        is left alone. Returns how many closed."""
        if not task_ids:
            return 0
        now = timezone.now()
        return NodeRun.objects.filter(
            id__in=list(task_ids), status=NodeRunStatus.PROCESSING, leased_by=self.worker_id
        ).update(status=status, result=result, settled_at=now, last_state_change_at=now)

    def park_batch(
        self,
        task_ids: Sequence[str],
        *,
        not_before: datetime.datetime,
        result: dict | None = None,
        restore_attempt: bool = False,
    ) -> int:
        """PROCESSING -> DEFERRED at a later window for a batch this
        worker holds, owner CAS. With `result` the park records a failed
        attempt (a transient delivery); with `restore_attempt` it hands
        the claim's attempt back, for a run that made no delivery (the
        row was not complete at claim, or its wait resolved to nothing)
        and must not walk toward the cap for it."""
        if not task_ids:
            return 0
        now = timezone.now()
        fields: dict = {
            "status": NodeRunStatus.DEFERRED,
            "not_before": not_before,
            "last_state_change_at": now,
            "leased_by": "",
        }
        if result is not None:
            fields["result"] = result
            fields["parked"] = True
        if restore_attempt:
            fields["attempts"] = models.F("attempts") - 1
        return NodeRun.objects.filter(
            id__in=list(task_ids), status=NodeRunStatus.PROCESSING, leased_by=self.worker_id
        ).update(**fields)

    @staticmethod
    def reclaim_stale_processing(*, now: datetime.datetime | None = None) -> int:
        """The reclaim scan: PROCESSING tasks whose owner went silent past the
        stale window (a dead consumer) return to READY for the provisioner
        to re-publish. The attempt is already counted, so a poison task
        still walks toward the cap. Deliberately does NOT touch QUEUED: a
        backed-up or offline consumer is normal and the transport is
        durable, so a QUEUED task drains on its own; re-handing it would
        only duplicate work the consumer's claim CAS already drops.

        A stale WEBHOOK run (a flush that died mid-batch) returns to
        DEFERRED instead: READY would hand it to the agent worker, which
        cannot run it. Its `not_before` is left as the window that was
        already due, so the next flush tick re-batches it."""
        now = now or timezone.now()
        stale_before = now - datetime.timedelta(seconds=PROCESSING_STALE_SECONDS)
        stale = NodeRun.objects.filter(status=NodeRunStatus.PROCESSING, last_state_change_at__lt=stale_before)
        released = {"processing_at": None, "leased_by": "", "last_state_change_at": now}
        agent_runs = stale.exclude(kind=WEBHOOK).update(status=NodeRunStatus.READY, **released)
        webhook_runs = stale.filter(kind=WEBHOOK).update(status=NodeRunStatus.DEFERRED, **released)
        return agent_runs + webhook_runs

"""The fill-task STATE MACHINE: READY -> QUEUED -> PROCESSING -> terminal,
the transitions the provisioner + shared consumer drive.

Every transition is a CAS `UPDATE` that also sets the state's own stamp
and `last_state_change_at` (the unified cursor the reclaim scan reads), so the
DB row is the single source of truth: no lease renewal (a bounded
run_cell + a stale `last_state_change_at` mean the owner is dead, not
slow) and no in-memory task state.

Both fill lanes ride this one flow: the autofill firehose (null-run
tasks) and the manual, fill-backed tasks. The provisioners pick READY
tasks (globally for autofill, per-fill for manual) and the shared
consumer claims/settles/parks them.

Not account-scoped: the workers are trusted processes; user-facing reads
live in fills.py.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator

from django.db import models
from django.utils import timezone

from ..constants import FILL_ROW_ATTEMPTS, FillTaskStatus
from ..models import FillTask

# The owner tolerates death, not slowness: a task PROCESSING longer than
# this was abandoned by a dead consumer (run_cell is timeout-bounded, so
# a live run always reaches a terminal state first). The reclaim scan reclaims
# past it. Sized to the worst-case single run the worker stop-grace
# already encodes.
PROCESSING_STALE_SECONDS = 35 * 60


class FillTaskFlow:
    """State transitions for one worker/consumer (its id stamps the
    PROCESSING claim, so only the owner settles what it claimed)."""

    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id

    @staticmethod
    def iter_ready(*, limit: int) -> Iterator[FillTask]:
        """The autofill provisioner's pick, LAZILY (single-pass): up to
        `limit` READY null-run (autofill) tasks that are due (a parked
        retry backs off in `not_before`), in (list_id, position, id)
        order. Grouping by list first is breadth-first across lists and
        is what `fill_task_autofill_idx` (partial on the null-run rows)
        serves so the LIMIT stops early, and the seam a sharded pick
        narrows to its lists. Streamed via .iterator() so a growing queue
        never materializes as one list."""
        now = timezone.now()
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        qs = FillTask.objects.filter(due, status=FillTaskStatus.READY, fill_run_id__isnull=True)
        yield from qs.defer("result").order_by("list_id", "position", "id")[:limit].iterator()

    @staticmethod
    def iter_ready_for_fill(fill_run_id: str, *, limit: int) -> Iterator[FillTask]:
        """The manual provisioner's per-fill pick, LAZILY (single-pass):
        up to `limit` of this fill's READY, due tasks in SHEET ORDER
        (position) so the fill marches top to bottom down the sheet the
        user is watching. A flat `limit` per fill is the fairness point:
        a wide fill cannot flood the bus ahead of a smaller one beside
        it. `fill_task_fill_idx` (fill_run_id equality, then position)
        serves it so the LIMIT stops early. Streamed via .iterator()."""
        now = timezone.now()
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        yield from (
            FillTask.objects.filter(due, fill_run_id=fill_run_id, status=FillTaskStatus.READY)
            .defer("result")
            .order_by("position", "id")[:limit]
            .iterator()
        )

    @staticmethod
    def mark_queued(task: FillTask) -> bool:
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
            FillTask.objects.filter(
                id=task.id,
                status=FillTaskStatus.READY,
                last_state_change_at=task.last_state_change_at,
            ).update(
                status=FillTaskStatus.QUEUED,
                queued_at=now,
                last_state_change_at=now,
            )
            == 1
        )

    def claim(self, task_id: str) -> FillTask | None:
        """(READY | QUEUED) -> PROCESSING for this consumer. Accepts READY
        too, so a message that outran the provisioner's `mark_queued` (a
        crash between publish and mark) still runs. Stamps the owner
        (`leased_by`) so only this consumer settles it, and increments
        `attempts` HERE so a task that kills its consumer still exhausts.
        Returns the claimed task, or None when the CAS lost (a duplicate
        delivery, or another consumer / the reclaim scan got there first) and
        the message should be dropped."""
        now = timezone.now()
        claimed = FillTask.objects.filter(
            id=task_id,
            status__in=(FillTaskStatus.READY, FillTaskStatus.QUEUED),
        ).update(
            status=FillTaskStatus.PROCESSING,
            processing_at=now,
            last_state_change_at=now,
            leased_by=self.worker_id,
            attempts=models.F("attempts") + 1,
        )
        if claimed != 1:
            return None
        return FillTask.objects.get(id=task_id)

    @staticmethod
    def exhausted(task: FillTask) -> bool:
        """Whether this claim is one too many, read AFTER the claim
        stamped its attempt (so a crash mid-run still counts)."""
        return task.attempts > FILL_ROW_ATTEMPTS

    def settle(self, task_id: str, result: dict, *, status: FillTaskStatus) -> bool:
        """PROCESSING -> a terminal state, CAS on the owner stamp so a
        reclaimed task's original consumer misses silently. `result` is
        positional so a `partial(settle, task_id, status=...)` matches
        landing.py's `close(result)` contract (the one shared by every
        terminal writer). Returns whether the close landed; landing.py
        runs this inside its own transaction and rolls the sheet + cell
        writes back on a miss."""
        now = timezone.now()
        return (
            FillTask.objects.filter(
                id=task_id,
                status=FillTaskStatus.PROCESSING,
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
            FillTask.objects.filter(
                id=task_id,
                status=FillTaskStatus.PROCESSING,
                leased_by=self.worker_id,
            ).update(
                status=FillTaskStatus.READY,
                not_before=now + datetime.timedelta(seconds=backoff_seconds),
                parked=True,
                last_state_change_at=now,
                leased_by="",
                result=result,
            )
            == 1
        )

    @staticmethod
    def reclaim_stale_processing(*, now: datetime.datetime | None = None) -> int:
        """The reclaim scan: PROCESSING tasks whose owner went silent past the
        stale window (a dead consumer) return to READY for the provisioner
        to re-publish. The attempt is already counted, so a poison task
        still walks toward the cap. Deliberately does NOT touch QUEUED: a
        backed-up or offline consumer is normal and the transport is
        durable, so a QUEUED task drains on its own; re-handing it would
        only duplicate work the consumer's claim CAS already drops."""
        now = now or timezone.now()
        stale_before = now - datetime.timedelta(seconds=PROCESSING_STALE_SECONDS)
        return FillTask.objects.filter(
            status=FillTaskStatus.PROCESSING,
            last_state_change_at__lt=stale_before,
        ).update(
            status=FillTaskStatus.READY,
            processing_at=None,
            leased_by="",
            last_state_change_at=now,
        )

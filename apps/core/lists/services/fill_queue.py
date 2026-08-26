"""The fill queue: the WORKER's only seam (nothing else touches
leases).

Admission materialized one QUEUED FillTask per consented row, so the
queue is the work list, the pending signal, and the consent record at
once. A claim is a short stamping transaction (never a held lock),
terminal writes are CAS on the claimant's own stamp, and a fill
completes when no queued task remains.

Not account-scoped: the worker is a trusted process serving every
account's fills; the user-facing reads live in fills.py.
"""

from __future__ import annotations

import datetime
from typing import NamedTuple

from django.db import models, transaction
from django.utils import timezone

from ..constants import (
    FILL_CLAIM_BATCH,
    FILL_ROW_ATTEMPTS,
    LIVE_FILL_STATUSES,
    ROW_LEASE_STALE_SECONDS,
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillTask
from . import cell_truth


def live_fill_count(account_id: str) -> int:
    """The account's fills that are still live. Public because the
    BENCH lane shares this account cap: it must not reach into the
    fills domain to count them, nor re-spell which statuses count as
    live. It lives here rather than in fill_admission because that
    module imports agents, and agents importing it back would close a
    cycle."""
    return Fill.objects.filter(account_id=account_id, status__in=LIVE_FILL_STATUSES).count()


class ClaimedBatch(NamedTuple):
    fill: Fill
    tasks: list[FillTask]


class FillQueueService:
    def __init__(self, *, worker_id: str) -> None:
        # The claimant's stamp: lease CAS filters on it, so a reclaimed
        # task's ORIGINAL claimant misses its own terminal write.
        self.worker_id = worker_id

    @staticmethod
    def _stale_before(now: datetime.datetime) -> datetime.datetime:
        return now - datetime.timedelta(seconds=ROW_LEASE_STALE_SECONDS)

    def _claimable(self, fill_id: str, now: datetime.datetime):
        """Tasks a claim may take: QUEUED, not freshly leased (a fresh
        lease is a running task; silence past the window means the
        claimant is dead), and DUE (a parked task backs off in TIME
        rather than waiting out a lease it never held).

        Deliberately NOT filtered by attempts. A task that died at the
        cap would otherwise sit queued and unclaimable forever, and its
        fill could never complete. The cap is enforced at claim time
        instead, where one more claim turns it terminal."""
        lease_open = models.Q(leased_at__isnull=True) | models.Q(leased_at__lt=self._stale_before(now))
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        return FillTask.objects.filter(lease_open & due, fill_id=fill_id, status=FillTaskStatus.QUEUED)

    def live_fills(self) -> list[Fill]:
        """Every live fill, oldest first. A READ, not a claim: the
        task-level skip_locked claim is what arbitrates between
        workers, and the supervisor interleaves these rather than
        working one to completion.

        Deliberately unfiltered by claimable work. A fill whose tasks
        are all leased has nothing claimable but is very much running,
        and the supervisor needs it in hand to renew those leases;
        deciding a fill is drained is the supervisor's call, since only
        it knows what this process still has in flight."""
        return list(Fill.objects.filter(status__in=LIVE_FILL_STATUSES).order_by("id"))

    def claim_batch(self, fill: Fill, *, free_slots: int) -> ClaimedBatch:
        """Claim up to min(FILL_CLAIM_BATCH, free_slots) tasks in one
        short transaction, in SHEET ORDER so a fill marches top to
        bottom down the sheet the user is watching. A parked retry
        re-enters at its own position rather than behind every fresh
        task, which lands it near where the user is looking.

        Sized to free slots because only a RUNNING task crosses the
        seams that renew its lease; a queued-but-claimed task would
        read dead while merely waiting.

        `attempts` increments HERE, not at completion, so a task that
        kills its worker thread still exhausts across process
        restarts. Counting completions bounds nothing a crash reaches."""
        limit = max(0, min(FILL_CLAIM_BATCH, free_slots))
        if limit == 0:
            return ClaimedBatch(fill=fill, tasks=[])
        now = timezone.now()
        with transaction.atomic():
            tasks = list(
                self._claimable(str(fill.id), now)
                .order_by("position", "id")
                .select_for_update(skip_locked=True)[:limit]
            )
            ids = [task.id for task in tasks]
            if ids:
                FillTask.objects.filter(id__in=ids).update(
                    leased_at=now,
                    leased_by=self.worker_id,
                    attempts=models.F("attempts") + 1,
                    not_before=None,
                )
                Fill.objects.filter(id=fill.id, status=FillStatus.PENDING).update(status=FillStatus.RUNNING)
        for task in tasks:
            task.leased_at = now
            task.leased_by = self.worker_id
            task.attempts += 1
        return ClaimedBatch(fill=fill, tasks=tasks)

    @staticmethod
    def exhausted(task: FillTask) -> bool:
        """Whether this claim is one too many. Read AFTER the claim
        stamped its attempt, so the number survives a crash that the
        in-process death counter would not."""
        return task.attempts > FILL_ROW_ATTEMPTS

    def renew_leases(self, tasks: list[FillTask]) -> None:
        """The SUPERVISOR's bulk renewal, one UPDATE on the worker's
        own long-lived thread (seam-level bumps ran on the framework's
        ephemeral executor threads and leaked one connection per bump;
        tasks are timeout-bounded by construction, so a renewing-but-
        hung task cannot exist and silence still means DEAD). CAS on
        the claimant's stamp so reclaimed tasks stay reclaimed."""
        if not tasks:
            return
        FillTask.objects.filter(
            id__in=[task.id for task in tasks],
            leased_by=self.worker_id,
            status=FillTaskStatus.QUEUED,
        ).update(leased_at=timezone.now())

    def release_lease(self, task: FillTask) -> None:
        """A dead worker thread's cleanup (it claimed but cannot run):
        the task returns to claimable NOW instead of cooling for the
        whole stale window. Its attempt is already counted, so a
        poison task still walks toward the cap. CAS on the claimant's
        stamp."""
        FillTask.objects.filter(
            id=task.id,
            leased_by=self.worker_id,
            status=FillTaskStatus.QUEUED,
        ).update(leased_at=None, leased_by="")

    def fill_is_live(self, fill_id: str) -> bool:
        """The worker's pre-task liveness check (cancel granularity is
        between tasks; in-flight spend is sunk cost, stated openly)."""
        return Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).exists()

    def complete_task(
        self,
        fill: Fill,
        task: FillTask,
        *,
        states: dict[str, StoredCellState],
        result: dict,
    ) -> bool:
        """Close a task and diagnose its cells, in one transaction.

        CAS on the claimant's own lease stamp, so a stale reclaim's
        original worker misses silently. Returns whether the write
        landed; a miss is the caller's cue to roll back everything it
        staged alongside, the sheet value included, so a value and a
        diagnosis can never come from different attempts.

        `causes` is PER COLUMN (a run answers outputs independently)
        and covers only the columns that did NOT land a value;
        `answered` is the ones that did, whose diagnoses are cleared."""
        with transaction.atomic():
            landed = FillTask.objects.filter(
                id=task.id,
                leased_by=self.worker_id,
                status=FillTaskStatus.QUEUED,
            ).update(
                status=FillTaskStatus.DONE,
                result=result,
                leased_at=None,
                leased_by="",
            )
            if landed == 1:
                cell_truth.write(fill, row_id=task.row_id, states=states)
        return landed == 1

    def park_task(self, task: FillTask, *, backoff_seconds: int) -> bool:
        """A 429 or timeout: the task stays QUEUED and comes round
        again after a real backoff, rather than waiting out a lease it
        never held. NOTHING is diagnosed, because nothing terminal
        happened: a parked cell is still pending and still shimmers.

        Exhaustion is not decided here. A task at the cap is claimed
        one more time and written terminal by the claimer, so the same
        rule covers a task that exhausted its retries and one that died
        mid-run at the cap."""
        return (
            FillTask.objects.filter(
                id=task.id,
                leased_by=self.worker_id,
                status=FillTaskStatus.QUEUED,
            ).update(
                leased_at=None,
                leased_by="",
                not_before=timezone.now() + datetime.timedelta(seconds=backoff_seconds),
                parked=True,
            )
            == 1
        )

    def bump(self, fill_id: str, **deltas: int) -> None:
        """Per-task progress plus the heartbeat stamp: ONE unlocked
        UPDATE with F() expressions. Counters are integer columns
        precisely so 64 threads can increment them without meeting on
        this row; a JSON dict would need select_for_update and a
        read-modify-write, which serializes the whole pool on the
        fill's hottest row.

        Delta keys: attempted, filled, blank, transient, row_seconds,
        search_wait_seconds."""
        Fill.objects.filter(id=fill_id).update(
            heartbeat_at=timezone.now(),
            updated_at=timezone.now(),
            **{key: models.F(key) + delta for key, delta in deltas.items()},
        )

    def set_concurrency_point(self, fill_id: str, point: int) -> None:
        """The AIMD gauge OVERWRITES (it is the operating point right
        now, not a sum), so it cannot ride the F() bump above."""
        Fill.objects.filter(id=fill_id).update(concurrency_point=point, heartbeat_at=timezone.now())

    def try_finish(self, fill_id: str) -> bool:
        """The worker's leg of the completion rule; see try_finish."""
        return try_finish(fill_id)

    def cancel_fill(self, fill_id: str) -> bool:
        """The worker's list-gone resolution: a user deletion reads as
        CANCELLED, never failed (failed is config-tier and carries an
        error the UI dresses as a failure story). Same transition as the
        user cancel; a fill already terminal stays put."""
        return stop_fill(fill_id, FillStatus.CANCELLED)

    def fail_fill(self, fill_id: str, *, code: str, message: str) -> bool:
        """The breaker path (config-tier: a dead or throttling provider
        fails the whole fill loudly). From live states only; a cancel
        that already landed stays cancelled."""
        return stop_fill(fill_id, FillStatus.FAILED, code=code, message=message)


def try_finish(fill_id: str) -> bool:
    """THE completion rule, shared by the worker's drain and admission,
    so the two cannot disagree about when a fill is done: a fill flips
    COMPLETE when no QUEUED task remains.

    Monotonic by construction, because nothing creates tasks after
    admission: the set only ever shrinks, so the check cannot go stale
    between reading and flipping. A stale-leased task is still queued,
    so a crashed claimant never fakes completion.

    Admission needs it because a fill can be born drained: every row it
    consented to was answered on the bench, so nothing is claimable and
    no worker would ever visit it. That is the same question the worker
    asks after its last row, and asking it in two places is how the two
    answers drift.

    Module level rather than a queue method because it reads no worker
    identity: a fill is finished or it is not, whoever is asking."""
    with transaction.atomic():
        fill = Fill.objects.select_for_update().filter(id=fill_id, status__in=LIVE_FILL_STATUSES).first()
        if fill is None:
            return False
        if FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.QUEUED).exists():
            return False
        fill.status = FillStatus.COMPLETE
        fill.save(update_fields=["status", "updated_at"])
        return True


def stop_fill(fill_id: str, status: FillStatus, *, code: str = "", message: str = "") -> bool:
    """THE terminal transition, shared by the worker's paths and the
    user's cancel, so the two cannot order their writes differently.

    The QUEUE IS SWEPT FIRST, then the fill flips. That order is
    load-bearing: the terminal write path takes FillTask before Fill,
    so flipping the fill first would invert it and deadlock. A worker
    that claims a task in the window between the two is harmless,
    because its terminal CAS finds the task abandoned.

    Nothing on the sheet is touched. Every cell this fill would have
    reached was pending only because a QUEUED task said so, so
    abandoning the tasks is what stops the shimmer, and there is no
    state to sweep back.
    """
    with transaction.atomic():
        if not Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).exists():
            return False
        released = _abandon_queued(fill_id)
        # The released rows ride the SAME update as the status, so the
        # queue-then-fill lock order the docstring above depends on is
        # one write per table, not two.
        flipped = Fill.objects.filter(id=fill_id, status__in=LIVE_FILL_STATUSES).update(
            status=status,
            error_code=code,
            error_message=message,
            updated_at=timezone.now(),
            transient=models.F("transient") - released,
        )
    return flipped == 1


def _abandon_queued(fill_id: str) -> int:
    """Consent granted and not spent, recorded rather than deleted: it
    is the only honest answer to what a stopped fill still owed, and a
    later resume reads it instead of reconstructing it.

    Returns how many PARKED rows it abandoned, so the caller can
    release them from the fill's transient gauge in the same update
    that flips the status. This is the THIRD terminal writer: a parked
    task leaves QUEUED either through complete_task, which decrements,
    or through here, and try_finish refuses to complete a fill while
    anything is still queued, so there is no other exit. It matters
    most on the fills likeliest to have parked rows: the throttle
    breaker fails a fill precisely when they are.

    Counted AFTER the sweep, over what the sweep produced. A row a
    worker completed in between reads DONE either way, so it is not in
    this set and its own terminal write already released it. And a park
    attempted after the sweep holds the row locks finds its
    status=QUEUED CAS matching nothing, returns False, and never bumps
    the gauge, so there is nothing counted here that was not
    incremented and nothing incremented that is not counted. Reading
    the ids BEFORE the sweep would not hold: a row parked between the
    read and the sweep would be abandoned without ever being
    released."""
    FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.QUEUED).update(
        status=FillTaskStatus.ABANDONED, leased_at=None, leased_by="", updated_at=timezone.now()
    )
    return FillTask.objects.filter(fill_id=fill_id, status=FillTaskStatus.ABANDONED, parked=True).count()

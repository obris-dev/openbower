"""The fill queue: TASK lifecycle, and only that (nothing else touches
leases).

Admission materialized one QUEUED FillTask per consented row, so the
queue is the work list, the pending signal, and the consent record at
once. A claim is a short stamping transaction (never a held lock),
terminal writes are CAS on the claimant's own stamp. What happens to
the FILL as its rows resolve (progress counters, the status machine)
is fill_progress.py's; what lands on the sheet is landing.py's.

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
    ROW_LEASE_STALE_SECONDS,
    FillStatus,
    FillTaskStatus,
)
from ..models import Fill, FillTask


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

    def _claimable(self, fill_run_id: str, now: datetime.datetime):
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
        return FillTask.objects.filter(lease_open & due, fill_run_id=fill_run_id, status=FillTaskStatus.QUEUED)

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
                # The park stores the WHOLE run on `result` (cells,
                # evidence, searches); a claim needs none of it, and a
                # throttle storm is exactly when most tasks are parked
                # and claims are most frequent. The one reader
                # (_give_up) pays a lazy load on its rare branch.
                .defer("result")
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

    def _claimable_autofill(self, now: datetime.datetime):
        """The automatic path's claimables: QUEUED null-run tasks (no
        Fill), same lease-open + due rules as _claimable but not scoped
        to a fill."""
        lease_open = models.Q(leased_at__isnull=True) | models.Q(leased_at__lt=self._stale_before(now))
        due = models.Q(not_before__isnull=True) | models.Q(not_before__lte=now)
        return FillTask.objects.filter(lease_open & due, fill_run_id__isnull=True, status=FillTaskStatus.QUEUED)

    def claim_autofill_batch(self, *, free_slots: int) -> list[FillTask]:
        """Claim up to min(FILL_CLAIM_BATCH, free_slots) null-run tasks
        in one short transaction, OLDEST FIRST (autofill tasks carry no
        sheet position, so id is their arrival order). No Fill to flip:
        an autofill task belongs to no run. Otherwise identical to
        claim_batch: skip_locked, the lease stamp, attempts++ at claim."""
        limit = max(0, min(FILL_CLAIM_BATCH, free_slots))
        if limit == 0:
            return []
        now = timezone.now()
        with transaction.atomic():
            tasks = list(
                self._claimable_autofill(now).defer("result").order_by("id").select_for_update(skip_locked=True)[:limit]
            )
            ids = [task.id for task in tasks]
            if ids:
                FillTask.objects.filter(id__in=ids).update(
                    leased_at=now,
                    leased_by=self.worker_id,
                    attempts=models.F("attempts") + 1,
                    not_before=None,
                )
        for task in tasks:
            task.leased_at = now
            task.leased_by = self.worker_id
            task.attempts += 1
        return tasks

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

    def complete_task(self, task: FillTask, result: dict) -> bool:
        """Close a claimed task DONE with its run stored on it. CAS on
        the claimant's own lease stamp, so a stale reclaim's original
        worker misses silently. Returns whether the close landed; the
        landing (services/landing.py) runs this inside its own
        transaction and rolls the sheet and cell writes back on a miss,
        so a value and a diagnosis can never come from different
        attempts. The queue writes nothing to the sheet or the ledger
        itself."""
        return (
            FillTask.objects.filter(
                id=task.id,
                leased_by=self.worker_id,
                status=FillTaskStatus.QUEUED,
            ).update(
                status=FillTaskStatus.DONE,
                result=result,
                leased_at=None,
                leased_by="",
            )
            == 1
        )

    def mark_row_missing(self, task: FillTask) -> bool:
        """Close a task whose row no longer exists: terminal, with no
        cell to diagnose and nothing a resume could owe. The same CAS
        as the other terminal writes, so a reclaimed lease misses."""
        return (
            FillTask.objects.filter(
                id=task.id,
                leased_by=self.worker_id,
                status=FillTaskStatus.QUEUED,
            ).update(
                status=FillTaskStatus.ROW_MISSING,
                result={},
                leased_at=None,
                leased_by="",
            )
            == 1
        )

    def mark_list_missing(self, task: FillTask) -> bool:
        """Close an autofill task whose LIST is gone (deleted after the
        row was pushed): terminal, nothing to diagnose. A fill-backed
        task never reaches this (its Fill was swept with the list); it
        is the autofill worker's answer to an orphaned task instead of a
        delete-cascade off the list. Same CAS as the other terminals."""
        return (
            FillTask.objects.filter(
                id=task.id,
                leased_by=self.worker_id,
                status=FillTaskStatus.QUEUED,
            ).update(
                status=FillTaskStatus.LIST_MISSING,
                result={},
                leased_at=None,
                leased_by="",
            )
            == 1
        )

    def park_task(self, task: FillTask, *, backoff_seconds: int, result: dict) -> bool:
        """A 429 or timeout (the model's, or a search provider's): the task
        stays QUEUED and comes round again after a real backoff, rather
        than waiting out a lease it never held. NOTHING is diagnosed on
        the sheet, because nothing terminal happened: a parked cell is
        still pending and still shimmers. The run IS stored (`result`,
        the CellRunResult dump): the refusals it records are the audit,
        and the give-up path reads its cause.

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
                result=result,
            )
            == 1
        )

"""The job runner: one tick claims the jobs that are due and gives each
slices of work until it is done or the tick's budget is spent, then
parks or settles it. The ONLY writer of `Job.status`, so every
transition is findable here.

Every transition is a compare-and-set UPDATE on the status, so two
ticks overlapping (a slow slice past the minute) split the due jobs
between them instead of both running one. A tick that dies leaves its
jobs PROCESSING; the next tick's reclaim returns them to READY past the
stale window, and they resume from their cursor. `attempts` counts
UNEXPECTED exits and nothing else: a slice that raises (parked with a
backoff and its cause) and a dead tick (seen by the reclaim). Running
out of budget is neither. At the cap the job is FAILED with the last
cause.

Safe to miss (a job waits) and safe to double (the CAS). Not
account-scoped: a trusted process, like the node-run flows."""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass

from django.db import DatabaseError, models
from django.utils import timezone

from ..constants import (
    JOB_ATTEMPTS,
    JOB_ERROR_MAX_LENGTH,
    JOB_RETRY_BACKOFF_SECONDS,
    JOB_STALE_SECONDS,
    JOB_TICK_BUDGET_SECONDS,
    JobStatus,
)
from ..kinds import registry
from ..models import Job

logger = logging.getLogger(__name__)

EXHAUSTED = f"Gave up after {JOB_ATTEMPTS} attempts; the last one did not finish."


@dataclass
class TickReport:
    """One TICK's tallies for the command's log line: how many jobs the
    runner touched and where it left them. Nothing about what any job
    did; that is the kind's, on the job's own `progress`."""

    reclaimed: int = 0
    claimed: int = 0
    done: int = 0
    parked: int = 0
    failed: int = 0


class JobRunner:
    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id

    def tick(
        self, *, now: datetime.datetime | None = None, budget_seconds: float = JOB_TICK_BUDGET_SECONDS
    ) -> TickReport:
        """Reclaim what a dead tick left, then work the due jobs oldest
        first until the budget is spent. The budget bounds the TICK, so
        a long job yields to the next minute rather than starving the
        jobs behind it."""
        now = now or timezone.now()
        deadline = time.monotonic() + budget_seconds
        report = TickReport(reclaimed=self._reclaim(now))
        for job_id in self._due(now):
            # The clock is checked AFTER work, never before the first
            # claim: a tick always makes progress on at least one job,
            # so a starved budget slows the queue rather than stalling it.
            if report.claimed and time.monotonic() >= deadline:
                break
            job = self._claim(job_id, now)
            if job is None:
                continue
            report.claimed += 1
            self._work(job, deadline=deadline, report=report)
        return report

    def _work(self, job: Job, *, deadline: float, report: TickReport) -> None:
        try:
            kind = registry.parse_payload(job.kind, job.payload)
            while True:
                cursor = kind.run(job)
                if cursor is None:
                    report.done += self._settle(job)
                    return
                job.progress = cursor
                # The cursor is durable after EVERY slice, so a crash
                # loses one slice at most and a reclaimed job resumes
                # from where its dead tick actually got to.
                Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(progress=cursor)
                if time.monotonic() >= deadline:
                    # Out of tick, not out of luck: no attempt is spent.
                    report.parked += self._park(job, scheduled_at=timezone.now())
                    return
        except DatabaseError:
            # The connection is the tick's; nothing here recovers it.
            raise
        except Exception as e:
            # One job's crash (a payload that no longer parses, a bug in
            # its kind) must not stop the jobs behind it: count the
            # attempt, and park it with its cause or fail it at the cap.
            logger.exception("jobs: %s slice raised", job)
            cause = f"{type(e).__name__}: {e}"[:JOB_ERROR_MAX_LENGTH]
            attempts = job.attempts + 1
            if attempts >= JOB_ATTEMPTS:
                report.failed += self._fail(job, cause, attempts=attempts)
                return
            backoff = timezone.now() + datetime.timedelta(seconds=JOB_RETRY_BACKOFF_SECONDS)
            report.parked += self._park(job, scheduled_at=backoff, error=cause, attempts=attempts)

    # Transitions: each one UPDATE whose predicate is the status.

    @staticmethod
    def _due(now: datetime.datetime) -> list[str]:
        due = models.Q(scheduled_at__isnull=True) | models.Q(scheduled_at__lte=now)
        ready = Job.objects.filter(due, status=JobStatus.READY).order_by("id")
        return [str(job_id) for job_id in ready.values_list("id", flat=True)]

    @staticmethod
    def _claim(job_id: str, now: datetime.datetime) -> Job | None:
        claimed = Job.objects.filter(id=job_id, status=JobStatus.READY).update(
            status=JobStatus.PROCESSING,
            processing_at=now,
            last_state_change_at=now,
        )
        if claimed != 1:
            return None
        return Job.objects.get(id=job_id)

    @staticmethod
    def _settle(job: Job) -> int:
        now = timezone.now()
        return Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(
            status=JobStatus.DONE, progress=job.progress, error="", settled_at=now, last_state_change_at=now
        )

    @staticmethod
    def _park(job: Job, *, scheduled_at: datetime.datetime, error: str = "", attempts: int | None = None) -> int:
        fields: dict = {
            "status": JobStatus.READY,
            "progress": job.progress,
            "scheduled_at": scheduled_at,
            "error": error,
            "last_state_change_at": timezone.now(),
        }
        if attempts is not None:
            fields["attempts"] = attempts
        return Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(**fields)

    @staticmethod
    def _fail(job: Job, error: str, *, attempts: int) -> int:
        now = timezone.now()
        return Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(
            status=JobStatus.FAILED, error=error, attempts=attempts, settled_at=now, last_state_change_at=now
        )

    @staticmethod
    def _reclaim(now: datetime.datetime) -> int:
        """PROCESSING past the stale window means a dead tick, the one
        unexpected exit no except block sees: it counts an attempt here.
        Under the cap the job returns to READY, due now, cursor as it
        was; at the cap it is FAILED, so a job that keeps killing its
        tick stops."""
        stale_before = now - datetime.timedelta(seconds=JOB_STALE_SECONDS)
        stale = Job.objects.filter(status=JobStatus.PROCESSING, last_state_change_at__lt=stale_before)
        failed = stale.filter(attempts__gte=JOB_ATTEMPTS - 1).update(
            status=JobStatus.FAILED,
            attempts=models.F("attempts") + 1,
            error=EXHAUSTED,
            settled_at=now,
            last_state_change_at=now,
        )
        reclaimed = stale.update(
            status=JobStatus.READY,
            attempts=models.F("attempts") + 1,
            processing_at=None,
            scheduled_at=None,
            last_state_change_at=now,
        )
        return failed + reclaimed

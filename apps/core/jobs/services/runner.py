"""The job runner: one tick claims the jobs that are due and gives each
slices of work until it is done or the tick's budget is spent, then
parks or settles it. Every transition it makes is findable here; the
one writer from outside is JobService.Global.stop.

Every transition is a compare-and-set UPDATE on the status, so two
ticks overlapping (a slow slice past the minute) split the due jobs
between them instead of both running one. A tick that dies leaves its
jobs PROCESSING; the next tick's reclaim returns them to READY past the
stale window, and they resume from their cursor. `attempts` counts
UNEXPECTED exits and nothing else: a slice that raises (parked with a
backoff and its cause) and a dead tick (seen by the reclaim). Running
out of budget is neither, and neither is a kind's own wait (a slice
raising `JobWaiting` parks the job until it asked to be woken). At the
cap the job is FAILED with the last cause; a slice raising `JobFailed`
fails it at once with the kind's own code and copy.

The runner is the only writer of a job's status FROM INSIDE (claim,
park, settle, fail, reclaim); a stop from outside (JobService.Global.stop: a
user's cancel, a worker failing a fill) flips an open job terminal
under its own predicate, and every transition here is predicated on
PROCESSING, so a job stopped while a tick holds it is never resurrected
by that tick's park or settle.

Safe to miss (a job waits) and safe to double (the CAS). Not
account-scoped: a trusted process, like the node-run flows."""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass

from django.db import DatabaseError, models
from django.utils import timezone
from pydantic import ValidationError

from ..constants import (
    JOB_ATTEMPTS,
    JOB_ERROR_MAX_LENGTH,
    JOB_RETRY_BACKOFF_SECONDS,
    JOB_STALE_SECONDS,
    JOB_TICK_BUDGET_SECONDS,
    JobFailureCode,
    JobStatus,
)
from ..kinds import registry
from ..kinds.base import JobFailed, JobKind, JobWaiting
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
        kind = None
        try:
            kind = registry.parse_payload(job.kind, job.payload)
            # Typed at both edges: the stored cursor parses through the
            # kind's Progress here, and what run() returns is dumped
            # below, so the row never holds a shape the kind cannot read.
            progress = kind.Progress.model_validate(job.progress)
            while True:
                progress = kind.run(job, progress)
                if progress is None:
                    report.done += self._settle(job)
                    return
                job.progress = progress.model_dump(mode="json")
                # The cursor is durable after EVERY slice, so a crash
                # loses one slice at most and a reclaimed job resumes
                # from where its dead tick actually got to.
                Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(progress=job.progress)
                if time.monotonic() >= deadline:
                    # Out of tick, not out of luck: no attempt is spent.
                    report.parked += self._park(job, scheduled_at=timezone.now())
                    return
        except DatabaseError:
            # The connection is the tick's; nothing here recovers it.
            raise
        except JobWaiting as e:
            # Waiting on something outside the job: park until the kind
            # asked to be woken, the cursor as stored unless it gave a
            # new one. Not an attempt, and not this tick's problem
            # anymore.
            if e.progress is not None:
                job.progress = e.progress.model_dump(mode="json")
            wake = timezone.now() + datetime.timedelta(seconds=e.seconds)
            report.parked += self._park(job, scheduled_at=wake)
        except JobFailed as e:
            # The kind's own verdict: terminal on its terms, no attempt.
            # It tidies what it owns first, as a stop from outside does.
            self._tidy(job, kind)
            report.failed += self._fail(job, e.message, code=e.code, attempts=job.attempts)
        except Exception as e:
            # One job's crash (a payload that no longer parses, a bug in
            # its kind) must not stop the jobs behind it: count the
            # attempt, and park it with its cause or fail it at the cap.
            logger.exception("jobs: %s slice raised", job)
            cause = f"{type(e).__name__}: {e}"[:JOB_ERROR_MAX_LENGTH]
            attempts = job.attempts + 1
            if attempts >= JOB_ATTEMPTS:
                self._tidy(job, kind)
                report.failed += self._fail(job, cause, code=JobFailureCode.CRASHED, attempts=attempts)
                return
            backoff = timezone.now() + datetime.timedelta(seconds=JOB_RETRY_BACKOFF_SECONDS)
            report.parked += self._park(job, scheduled_at=backoff, error=cause, attempts=attempts)

    @staticmethod
    def _tidy(job: Job, kind: JobKind | None) -> None:
        """The kind's on_stop before a terminal write the runner makes on
        its own: what the job owns outside its row is swept, as it is
        when a stop comes from outside. A kind that could not be parsed
        has no tidy to run; a tidy that raises is logged and the fail
        stands, since the verdict is not the tidy's to veto."""
        if kind is None:
            try:
                kind = registry.parse_payload(job.kind, job.payload)
            except (KeyError, ValidationError):
                logger.warning("jobs: %s cannot be parsed; failing without its tidy", job)
                return
        try:
            kind.on_stop(job)
        except Exception:
            logger.exception("jobs: %s on_stop raised; the fail stands", job)

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
            status=JobStatus.DONE,
            progress=job.progress,
            error_code="",
            error="",
            settled_at=now,
            last_state_change_at=now,
        )

    @staticmethod
    def _park(
        job: Job, *, scheduled_at: datetime.datetime, error: str | None = None, attempts: int | None = None
    ) -> int:
        """`error` None leaves the stored cause alone (a budget park or a
        wait says nothing about failure); a raising slice writes its
        cause."""
        fields: dict = {
            "status": JobStatus.READY,
            "progress": job.progress,
            "scheduled_at": scheduled_at,
            "last_state_change_at": timezone.now(),
        }
        if error is not None:
            fields["error"] = error
        if attempts is not None:
            fields["attempts"] = attempts
        return Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(**fields)

    @staticmethod
    def _fail(job: Job, error: str, *, attempts: int, code: str = "") -> int:
        now = timezone.now()
        return Job.objects.filter(id=job.id, status=JobStatus.PROCESSING).update(
            status=JobStatus.FAILED,
            error_code=code,
            error=error,
            attempts=attempts,
            settled_at=now,
            last_state_change_at=now,
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
        exhausted = list(stale.filter(attempts__gte=JOB_ATTEMPTS - 1))
        failed = stale.filter(attempts__gte=JOB_ATTEMPTS - 1).update(
            status=JobStatus.FAILED,
            attempts=models.F("attempts") + 1,
            error_code=JobFailureCode.EXHAUSTED,
            error=EXHAUSTED,
            settled_at=now,
            last_state_change_at=now,
        )
        for job in exhausted:
            # Failed by a dead tick, so no slice tidies: the kind's sweep
            # runs here, after the flip, idempotent either way.
            JobRunner._tidy(job, None)
        reclaimed = stale.update(
            status=JobStatus.READY,
            attempts=models.F("attempts") + 1,
            processing_at=None,
            scheduled_at=None,
            last_state_change_at=now,
        )
        return failed + reclaimed

"""The job service. Account-scoped for the writes a request makes (a
job enters the queue for one account, attributed to the user who asked
or to nobody); `Global` for the trusted-process transitions from
OUTSIDE the runner (the workers, the kinds' own custody), by job id,
where the caller has already resolved the job account-scoped or serves
every account. The runner (`JobRunner`) owns every transition from
INSIDE a tick; this service owns the ones from outside."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..constants import OPEN_JOB_STATES, JobStatus
from ..kinds import registry
from ..kinds.base import JobKind
from ..models import Job


class JobGlobal:
    """The stop from outside: a user's cancel (resolved account-scoped
    by the caller first), or a worker that learned the job can never
    succeed (a fill whose config cannot run). One terminal transition
    for both, so the two cannot order their writes differently: the
    kind tidies what it owns first (`on_stop`, inside the transaction),
    then the status flips from an open state under its own predicate.
    A job a tick still holds is stopped too: the tick's park or settle
    is predicated on PROCESSING and misses, so the stop stands and the
    tick's slice is the last one."""

    @staticmethod
    def stop(job_id: str, *, status: JobStatus, code: str = "", message: str = "") -> bool:
        """Returns whether this call flipped it; a job already terminal
        is a no-op, not an error (the intent already holds)."""
        with transaction.atomic():
            job = Job.objects.filter(id=job_id, status__in=OPEN_JOB_STATES).first()
            if job is None:
                return False
            registry.parse_payload(job.kind, job.payload).on_stop(job)
            now = timezone.now()
            flipped = Job.objects.filter(id=job_id, status__in=OPEN_JOB_STATES).update(
                status=status, error_code=code, error=message, settled_at=now, last_state_change_at=now
            )
        return flipped == 1

    @staticmethod
    def cancel(job_id: str) -> bool:
        return JobGlobal.stop(job_id, status=JobStatus.CANCELLED)

    @staticmethod
    def fail(job_id: str, *, code: str, message: str) -> bool:
        return JobGlobal.stop(job_id, status=JobStatus.FAILED, code=code, message=message)


class JobService:
    Global = JobGlobal

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def enqueue(self, kind: JobKind, *, user_id: str, target_id: str = "") -> Job:
        """A READY job for the kind's payload, due now, attributed to
        the user who asked. Rides the caller's transaction when it has
        one, so a job is never visible before the write it works on (a
        column that does not exist yet). `target_id` is what the job
        works on (a fill's list), for the surfaces that page a target's
        jobs."""
        return self._enqueue(kind, user_id=user_id, target_id=target_id)

    def enqueue_system(self, kind: JobKind, *, target_id: str = "") -> Job:
        """A READY job no user asked for (a scheduled or derived walk):
        `user_id` is stored NULL, never a blank or a sentinel. Two entry
        points rather than an optional argument, so a call site says
        which it is and a system job can never be an attribution
        someone forgot."""
        return self._enqueue(kind, user_id=None, target_id=target_id)

    def _enqueue(self, kind: JobKind, *, user_id: str | None, target_id: str) -> Job:
        now = timezone.now()
        return Job.objects.create(
            account_id=self.account_id,
            user_id=user_id,
            target_id=target_id,
            kind=kind.KIND,
            payload=kind.model_dump(),
            status=JobStatus.READY,
            queued_at=now,
            last_state_change_at=now,
        )

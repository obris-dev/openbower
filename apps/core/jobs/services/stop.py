"""A stop from OUTSIDE the runner: a user's cancel, or a worker that
learned the job can never succeed (a fill whose config cannot run).
One terminal transition for both, so the two cannot order their
writes differently: the kind tidies what it owns first (`on_stop`,
inside the transaction), then the status flips from an open state
under its own predicate. A job a tick still holds is stopped too: the
tick's park or settle is predicated on PROCESSING and misses, so the
stop stands and the tick's slice is the last one. Not account-scoped:
the caller resolved the job account-scoped before asking."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..constants import OPEN_JOB_STATES, JobStatus
from ..kinds import registry
from ..models import Job


def stop(job_id: str, *, status: JobStatus, code: str = "", message: str = "") -> bool:
    """Flip an open job to `status` (FAILED with its two-tier error, or
    CANCELLED). Returns whether this call flipped it; a job already
    terminal is a no-op, not an error (the intent already holds)."""
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


def cancel(job_id: str) -> bool:
    return stop(job_id, status=JobStatus.CANCELLED)


def fail(job_id: str, *, code: str, message: str) -> bool:
    return stop(job_id, status=JobStatus.FAILED, code=code, message=message)

"""The one way a job enters the queue."""

from __future__ import annotations

from django.utils import timezone

from ..constants import JobStatus
from ..kinds.base import JobKind
from ..models import Job


def enqueue(account_id: str, kind: JobKind) -> Job:
    """A READY job for the kind's payload, due now. Rides the caller's
    transaction when it has one, so a job is never visible before the
    write it works on (a column that does not exist yet)."""
    now = timezone.now()
    return Job.objects.create(
        account_id=account_id,
        kind=kind.KIND,
        payload=kind.model_dump(),
        status=JobStatus.READY,
        queued_at=now,
        last_state_change_at=now,
    )

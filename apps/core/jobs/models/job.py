from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import JOB_ERROR_MAX_LENGTH, JOB_KIND_MAX_LENGTH, JOB_STATUS_MAX_LENGTH, JobStatus


class Job(AccountScopedModel):
    """One unit of background work, AND the queue itself: what was
    asked (`kind`, `payload`), where it stands (`status`, `progress`),
    and what happened (`error`, the stamps). A job is bounded work with
    a RESUME CURSOR: its kind does one slice at a time and hands back
    where it stopped, so a job survives a tick's budget, a crash, and a
    retry by starting from `progress`, never from the beginning.

    This is deliberately NOT a NodeRun. A run is one node applied to one
    sheet row, claimed and settled as one unit; a job is an operation
    whose OUTPUT may be many runs (a webhook backfill) or a file (an
    export), and its lifecycle belongs to no row. Two ledgers, one
    claim discipline (a status CAS), so a reader of either never meets
    a member that is not its kind of thing.

    `payload` and `progress` are the kind's own typed models, dumped on
    write and parsed back by the kind; the runner treats both as
    opaque. No worker stamp: a stale job the reclaim returned to READY
    can be settled late by its first tick, which lands on top of the
    second tick's settle; every kind's slice is idempotent, so the cost
    is one re-walked slice, and the column stays out until a kind with
    a non-idempotent slice earns it."""

    kind = models.CharField(_("kind"), max_length=JOB_KIND_MAX_LENGTH)
    payload = models.JSONField(_("payload"), default=dict)
    progress = models.JSONField(_("progress"), default=dict)
    status = models.CharField(_("status"), max_length=JOB_STATUS_MAX_LENGTH, default=JobStatus.READY)
    # Incremented AT CLAIM, so a slice that kills its tick still counts
    # toward the cap.
    attempts = models.IntegerField(_("attempts"), default=0)
    # When a parked job becomes claimable again: the backoff after a
    # slice raised, or now for a job that yielded on the tick's budget.
    not_before = models.DateTimeField(_("not before"), null=True, blank=True)
    error = models.CharField(_("error"), max_length=JOB_ERROR_MAX_LENGTH, blank=True, default="")
    queued_at = models.DateTimeField(_("queued at"), null=True, blank=True)
    processing_at = models.DateTimeField(_("processing at"), null=True, blank=True)
    settled_at = models.DateTimeField(_("settled at"), null=True, blank=True)
    last_state_change_at = models.DateTimeField(_("last state change at"), null=True, blank=True)

    class Meta:
        verbose_name = _("job")
        verbose_name_plural = _("jobs")
        constraints = [
            models.CheckConstraint(condition=~models.Q(kind=""), name="job_kind_named"),
        ]
        indexes = [
            # The tick's pick: READY jobs in id (age) order, `not_before`
            # on the leaf so a parked job is rejected without a heap
            # fetch. Partial, so settled history never widens it.
            models.Index(
                fields=["status", "id"],
                include=["not_before"],
                name="job_ready_idx",
                condition=models.Q(status=JobStatus.READY),
            ),
            # The reclaim's scan: PROCESSING jobs by how long they have
            # been held.
            models.Index(
                fields=["status", "last_state_change_at"],
                name="job_reclaim_idx",
                condition=models.Q(status=JobStatus.PROCESSING),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.id} ({self.status})"

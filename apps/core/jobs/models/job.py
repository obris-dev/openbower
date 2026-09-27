from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import (
    JOB_ERROR_CODE_MAX_LENGTH,
    JOB_ERROR_MAX_LENGTH,
    JOB_KIND_MAX_LENGTH,
    JOB_STATUS_MAX_LENGTH,
    OPEN_JOB_STATES,
    JobStatus,
)


class Job(AccountScopedModel):
    """One unit of background work, AND the queue itself: what was
    asked (`kind`, `payload`), where it stands (`status`, `progress`),
    and what happened (`error_code`, `error`, the stamps). A job is
    bounded work with a RESUME CURSOR: its kind does one slice at a
    time and hands back where it stopped, so a job survives a tick's
    budget, a crash, and a retry by starting from `progress`, never
    from the beginning. A kind that waits on something else (a fill
    waiting for its runs to settle) parks itself for a while and is
    picked up again when due; the wait spends no attempt.

    This is deliberately NOT a NodeRun. A run is one node applied to one
    sheet row, claimed and settled as one unit; a job is an operation
    whose OUTPUT may be many runs (a fill, a webhook backfill) or a file
    (an export), and its lifecycle belongs to no row. Two ledgers, one
    claim discipline (a status CAS), so a reader of either never meets
    a member that is not its kind of thing.

    `payload` and `progress` are the kind's own typed models, dumped on
    write and parsed back by the kind; the runner treats both as
    opaque. `user_id` is who asked, ATTRIBUTION only (never an access
    filter), NULL when no user did (a job the system queued); `target_id`
    is what the job works on, in the
    kind's own terms (a fill's list; blank for a job with no one
    target), so a surface can page a target's jobs without reading
    payloads. No worker stamp: a stale job the reclaim returned
    to READY can be settled late by its first tick, which lands on top
    of the second tick's settle; every kind's slice is idempotent, so
    the cost is one re-walked slice, and the column stays out until a
    kind with a non-idempotent slice earns it."""

    kind = models.CharField(_("kind"), max_length=JOB_KIND_MAX_LENGTH)
    # NULL, never "", when no user asked: a system job has no owner to
    # spell, and a sentinel string would read as one.
    user_id = models.CharField(_("user id"), max_length=26, null=True, blank=True)
    target_id = models.CharField(_("target id"), max_length=26, blank=True, default="")
    payload = models.JSONField(_("payload"), default=dict)
    progress = models.JSONField(_("progress"), default=dict)
    status = models.CharField(_("status"), max_length=JOB_STATUS_MAX_LENGTH, default=JobStatus.READY)
    # UNEXPECTED exits only: a slice that raised, or a tick that died
    # holding the job (counted by the reclaim). A claim, a settle, a
    # park for running out of budget, and a kind's own wait leave it
    # alone, so a long job never walks toward the cap by being long.
    attempts = models.IntegerField(_("attempts"), default=0)
    # When the job is next due: null means now; a slice that raised
    # schedules it after a backoff, a job that yielded on the tick's
    # budget for now, a kind that is waiting for when it asked to be
    # woken, a reclaimed job for now.
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True)
    # The two-tier error on FAILED: the code is the machine leg (a
    # kind's own vocabulary; "" for a crash the runner caught), the
    # message is copy a client renders verbatim.
    error_code = models.CharField(_("error code"), max_length=JOB_ERROR_CODE_MAX_LENGTH, blank=True, default="")
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
            # The tick's pick: READY jobs in id (age) order, `scheduled_at`
            # on the leaf so a parked job is rejected without a heap
            # fetch. Partial, so settled history never widens it.
            models.Index(
                fields=["status", "id"],
                include=["scheduled_at"],
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
            # A kind's OPEN jobs, whatever the account or target, in age
            # order: the fill provisioner's loop reads this every pass.
            # Partial on the open states, so the scan is bounded by what
            # is live rather than by every job the kind has ever run;
            # the other kind-leading index carries `target_id` second,
            # which a global read has nothing to seek on.
            models.Index(
                fields=["kind", "id"],
                name="job_open_idx",
                condition=models.Q(status__in=OPEN_JOB_STATES),
            ),
            # A target's jobs of one kind by status: the sheet's fills
            # page and every "which fills are open on this list" read.
            models.Index(fields=["kind", "target_id", "status", "-id"], name="job_target_idx"),
            # An account's jobs of one kind by status: the fill cap.
            models.Index(fields=["account_id", "kind", "status"], name="job_account_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.id} ({self.status})"

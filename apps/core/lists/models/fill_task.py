from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import (
    FILL_TASK_STATUS_MAX_LENGTH,
    LEASED_BY_MAX_LENGTH,
    FillTaskStatus,
)


class FillTask(AccountScopedModel):
    """One consented agent run, AND the queue itself.

    One task per sheet row, materialized at admission, so the queue is
    simultaneously the work list, the pending signal the sheet renders
    from, and the durable record of consent. That third fill is why the
    rows survive their fill: a cancelled fill's ABANDONED tasks are the
    only honest answer to "what did this still owe", which a queue
    holding only what a planner had reached could not give.

    `status` speaks about the WORK and never about the answer; the
    answer is diagnosed per cell on FillCellState. No word appears in
    both vocabularies, which is what keeps them from reading as copies
    of each other.

    AccountScopedModel, so `account_id` is DENORMALIZED from the owning
    fill. Every read still resolves that fill account-scoped, so this
    is defence in depth rather than the primary guard: scoping by
    convention holds only while every call site remembers, and one did
    not (a resume leg read another account's rows by a request-supplied
    id). Carrying the account makes an unscoped query a thing you have
    to write on purpose."""

    fill_id = models.CharField(_("fill id"), max_length=26)
    row_id = models.CharField(_("row id"), max_length=26)
    # The row's sheet position, snapshot-coherent (positions are
    # append-only): claims order by it, so a fill marches TOP TO BOTTOM
    # down the sheet the user is watching.
    position = models.IntegerField(_("position"), default=0)
    status = models.CharField(_("status"), max_length=FILL_TASK_STATUS_MAX_LENGTH, default=FillTaskStatus.QUEUED)
    # Incremented AT CLAIM, not at completion, so a row that kills its
    # worker thread still exhausts across process restarts. Counting
    # completions instead bounds nothing a crash can reach.
    attempts = models.IntegerField(_("attempts"), default=0)
    # When a parked task becomes claimable again: real backoff, rather
    # than waiting out a lease the task never held.
    not_before = models.DateTimeField(_("not before"), null=True, blank=True)
    # Whether a park has ever counted this task into its fill's
    # `transient` gauge. STORED, because both proxies for it are wrong
    # in opposite directions: `attempts` climbs at CLAIM, so a released
    # lease or a stale reclaim raises it with no park behind it, and
    # `not_before` is cleared by the next claim, so a task that parked
    # and then lost its worker reads as never parked. One is a gauge
    # that goes negative, the other one that never comes back down.
    # Set once, never cleared: it means counted, not currently waiting.
    parked = models.BooleanField(_("parked"), default=False)
    # The lease is a STAMP, never a held lock: the worker's supervising
    # loop renews every claimed task in bulk, so silence past
    # ROW_LEASE_STALE_SECONDS means the claimant is DEAD, not slow.
    leased_at = models.DateTimeField(_("leased at"), null=True, blank=True)
    leased_by = models.CharField(_("leased by"), max_length=LEASED_BY_MAX_LENGTH, blank=True, default="")
    # The serialized CellRun the runtime returned, verbatim: cells the
    # model answered, the evidence it saw, each search and whether it
    # failed, and per output its confidence and stated reason. ONE
    # column because the runtime returns ONE object and the bench lane
    # already stores it as one (AgentTestRun.result); splitting it here
    # is what left the model's ANSWERS with no home and pushed them
    # into the assessment dict.
    #
    # This is what the run PRODUCED. What LANDED is the sheet row plus
    # its diagnoses, and the difference between them is the audit story
    # (a value write-if-blank refused, an answer the floor dropped).
    result = models.JSONField(_("result"), default=dict)

    class Meta:
        verbose_name = _("fill task")
        verbose_name_plural = _("fill tasks")
        constraints = [
            # The idempotency key: enqueueing the same row twice is a
            # no-op. Also the row drawer's lookup.
            models.UniqueConstraint(fields=["fill_id", "row_id"], name="fill_task_fill_row_uniq"),
        ]
        indexes = [
            # The claim scan, and the completion probe. PARTIAL on
            # queued so it SHRINKS as the fill drains: claiming row
            # 24,900 of 25,000 costs what claiming row 1 did, and
            # "is this fill done" is an empty-index check.
            models.Index(
                fields=["fill_id", "position"],
                name="fill_task_claim_idx",
                condition=models.Q(status=FillTaskStatus.QUEUED),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fill_id}/{self.row_id} ({self.status})"

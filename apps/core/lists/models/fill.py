from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel

from ..constants import (
    CONFIG_FINGERPRINT_MAX_LENGTH,
    FILL_ERROR_CODE_MAX_LENGTH,
    FILL_ERROR_MESSAGE_MAX_LENGTH,
    FILL_STATUS_MAX_LENGTH,
    LIVE_FILL_STATUSES,
    FillStatus,
)


class Fill(UserScopedModel):
    """One durable fill of one AI column: the frozen record of what the
    user consented to spend, and how far it has got.

    The WORK is FillTask rows, materialized at admission, so this row
    carries no cursor and no lease. `user_id` (the base's attribution
    field) is the wire's started_by; authorization is account
    membership, never the starter."""

    list_id = models.CharField(_("list id"), max_length=26)
    agent_id = models.CharField(_("agent id"), max_length=26)
    # Bare CharField (see AgentTestRun.status): the enum lives in
    # constants and the services write it; choices= buys nothing.
    status = models.CharField(_("status"), max_length=FILL_STATUS_MAX_LENGTH, default=FillStatus.PENDING)
    # The columns this fill owns, frozen at consent. A LIST, not a
    # mapping: each output's own key IS its column key, so a dict would
    # advertise a translation that does not exist. It earns a dict back
    # the day an output can point at a differently named column.
    column_keys = models.JSONField(_("column keys"), default=list)
    # The FULL resolved config frozen at admission: results render with
    # the config that produced them; mid-fill agent edits never apply.
    config_snapshot = models.JSONField(_("config snapshot"), default=dict)
    # The snapshot's digest, frozen beside it: settled-under-config and
    # resume checks compare stored fields, never recompute snapshots.
    config_fingerprint = models.CharField(
        _("config fingerprint"), max_length=CONFIG_FINGERPRINT_MAX_LENGTH, blank=True, default=""
    )
    # Optional downward override on the AIMD ceiling; 0 = unset.
    concurrency = models.IntegerField(_("concurrency"), default=0)
    # The row count the user consented to; admission 409s when the
    # count changed (shrinkage included). The progress denominator on
    # every surface that shows one.
    confirmed_row_count = models.IntegerField(_("confirmed row count"))
    # Worker-written progress, never COUNT(*) polling. COLUMNS, not a
    # JSON dict: these are incremented once per completed task from 64
    # threads, so they update with F() expressions and no lock. A dict
    # would need a read-modify-write behind select_for_update on this
    # single row, which serializes the whole pool.
    attempted = models.IntegerField(_("attempted"), default=0)
    filled = models.IntegerField(_("filled"), default=0)
    blank = models.IntegerField(_("blank"), default=0)
    transient = models.IntegerField(_("transient"), default=0)
    # Pace accumulators across terminal tasks: total wall seconds and
    # the share of it parked on search. "What is slow" is their ratio.
    row_seconds = models.IntegerField(_("row seconds"), default=0)
    search_wait_seconds = models.IntegerField(_("search wait seconds"), default=0)
    # The AIMD operating point at the last write, so a restarted worker
    # resumes where the fill was instead of re-probing from the start.
    concurrency_point = models.IntegerField(_("concurrency point"), default=0)
    # Stamped with each counter write and once per supervisor pass, so
    # a healthy-but-slow fill never reads as an unreporting worker. The
    # client judges staleness against the wire's ROW_LEASE_STALE_SECONDS,
    # warning-role only.
    heartbeat_at = models.DateTimeField(_("heartbeat at"), null=True, blank=True)
    # The two-tier error on FAILED: code is the machine leg, message is
    # server-authored copy the client renders verbatim.
    error_code = models.CharField(_("error code"), max_length=FILL_ERROR_CODE_MAX_LENGTH, blank=True, default="")
    error_message = models.CharField(
        _("error message"), max_length=FILL_ERROR_MESSAGE_MAX_LENGTH, blank=True, default=""
    )

    class Meta:
        verbose_name = _("fill")
        verbose_name_plural = _("fills")
        indexes = [
            models.Index(fields=["account_id", "id"], name="fill_account_idx"),
            # The sheet's keyset page, and the one pass that names each
            # column's newest fill.
            models.Index(fields=["list_id", "-id"], name="fill_list_recent_idx"),
            # The worker's drain is GLOBAL across accounts, so its scan
            # must cost the number of LIVE fills rather than the number
            # of fills that ever existed.
            models.Index(
                fields=["id"],
                name="fill_live_idx",
                condition=models.Q(status__in=LIVE_FILL_STATUSES),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id} ({self.status})"

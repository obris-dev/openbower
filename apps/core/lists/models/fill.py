from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel

from ..constants import (
    CONFIG_FINGERPRINT_MAX_LENGTH,
    FILL_ERROR_CODE_MAX_LENGTH,
    FILL_ERROR_MESSAGE_MAX_LENGTH,
    FILL_KIND_MAX_LENGTH,
    FILL_STATUS_MAX_LENGTH,
    LIVE_FILL_STATUSES,
    FillKind,
    FillStatus,
)


class Fill(UserScopedModel):
    """One durable fill of one AI column: the frozen record of what the
    user consented to spend, and how far it has got.

    The WORK is NodeRun rows, materialized at admission, so this row
    carries no cursor and no lease. `user_id` (the base's attribution
    field) is the wire's started_by; authorization is account
    membership, never the starter."""

    # The OPERATING MODE (a FillKind value): "normal" writes a sheet,
    # "test" is the bench's one-row diagnostic landing its result on
    # its task. Same lifecycle either way, which is the point.
    kind = models.CharField(_("kind"), max_length=FILL_KIND_MAX_LENGTH, default=FillKind.NORMAL)
    # BLANK for a test run (its row is `row_data`; a test fill never
    # points at a sheet); always set for the normal kind.
    list_id = models.CharField(_("list id"), max_length=26, blank=True, default="")
    agent_id = models.CharField(_("agent id"), max_length=26, blank=True, default="")
    # An INLINE test run's rows: a LIST of hand-fed row dicts, each
    # bounded at admission by the wire's bench caps (REFUSED past
    # them, never truncated), each task's position indexing its row.
    # A list of ONE today (the bench sends one row), shaped for the
    # inline example-list test the drawer flow can grow into without a
    # backfill. Empty for the normal kind.
    row_data = models.JSONField(_("row data"), default=list)
    # Bare CharField: the enum lives in
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
    # The row count the user consented to; admission 409s when the
    # count changed (shrinkage included). The progress denominator on
    # every surface that shows one.
    confirmed_row_count = models.IntegerField(_("confirmed row count"))
    # No progress counters or heartbeat column: the wire's counters and
    # heartbeat DERIVE from the task rows and cell states at read time
    # (services.fills.derive_counters / derive_heartbeat), so there is
    # nothing to store here.
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
            # The sheet's keyset page (live runs only now;
            # current_fill_id is stored at admission, so no pass walks
            # history to name a column's newest fill).
            models.Index(fields=["list_id", "-id"], name="fill_list_recent_idx"),
            # The worker's drain is GLOBAL across accounts, so its scan
            # must cost the number of LIVE fills rather than the number
            # of fills that ever existed.
            models.Index(
                fields=["id"],
                name="fill_live_idx",
                condition=models.Q(status__in=LIVE_FILL_STATUSES),
            ),
            # The kind-scoped ACCOUNT read: the one-live-test guard
            # (account, kind=test, status live) lands here and filters
            # its tail over the handful of matches; general to any
            # future per-kind account query, unlike a partial pinned
            # to one kind. The cron sweep does NOT ride it (global by
            # design, no account term, so it scans): fine at test-fill
            # volumes, and the honest index for it, if that ever
            # changes, is a partial on kind=test over id.
            models.Index(fields=["account_id", "kind"], name="fill_account_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.id} ({self.status})"

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import LogSource


class LogFeed(AccountScopedModel):
    """What feeds a log (a sheet of kind log): a hosted signal's listener,
    whose matches become its rows, or a custom signal's webhook, whose
    pushes do. One per log, removed with it by the log's delete (no
    cascades), and the sheet doors refuse a log so nothing else can delete
    it from under its feed."""

    sheet_id = models.CharField(_("sheet id"), max_length=26)
    # db_default for a process still on the code before this column: every
    # log it creates is hosted.
    source = models.CharField(
        _("source"),
        max_length=16,
        default=LogSource.HOSTED.value,
        db_default=LogSource.HOSTED.value,
        help_text=_("LogSource"),
    )
    # A hosted feed's listener; blank for a webhook feed (a constraint holds it).
    listener_id = models.CharField(_("listener id"), max_length=26, blank=True, default="")
    # The signal a hosted feed logs, as the catalog names it: what chose the
    # sheet's base columns, and what reads each match into a row. Set at
    # create; blank for a webhook feed, whose columns the person declares.
    signal = models.CharField(_("signal"), max_length=32, blank=True, default="")
    # The last match appended (the signals service's poll cursor), blank
    # before the first: where the next read starts.
    cursor = models.CharField(_("cursor"), max_length=26, blank=True, default="")
    # When the signals service first answered that the listener is gone.
    # Final: a gone listener never comes back, so the log stops polling and
    # the person deletes it.
    listener_gone_at = models.DateTimeField(_("listener gone at"), null=True, blank=True)
    # The poller's queue: when the log is next due, and who holds it until
    # when. A claim is a compare-and-set on the lease; a lapsed lease is
    # free again, so a dead poller needs no reclaim.
    next_poll_at = models.DateTimeField(_("next poll at"), default=timezone.now)
    leased_until = models.DateTimeField(_("leased until"), null=True, blank=True)
    leased_by = models.CharField(_("leased by"), max_length=128, blank=True, default="")
    # When the poller last read the listener's matches through, null before
    # the first: what a log that keeps failing shows ("last read 3h ago").
    last_polled_at = models.DateTimeField(_("last polled at"), null=True, blank=True)

    class Meta:
        verbose_name = _("log feed")
        verbose_name_plural = _("log feeds")
        constraints = [
            # One feed per log.
            models.UniqueConstraint(fields=["sheet_id"], name="signals_log_feed_sheet_uniq"),
            # A hosted feed names the listener it reads.
            models.CheckConstraint(
                condition=~models.Q(source=LogSource.HOSTED.value) | ~models.Q(listener_id=""),
                name="signals_log_feed_hosted_listener",
            ),
        ]
        indexes = [
            models.Index(fields=["account_id", "id"], name="signals_log_feed_account_idx"),
            # The due list: live hosted logs only, longest waiting first.
            models.Index(
                fields=["next_poll_at", "id"],
                name="signals_log_feed_due_idx",
                condition=models.Q(listener_gone_at__isnull=True, source=LogSource.HOSTED.value),
            ),
        ]

    def __str__(self) -> str:
        if self.source == LogSource.WEBHOOK:
            return f"webhook -> {self.sheet_id}"
        return f"feed {self.listener_id} -> {self.sheet_id}"

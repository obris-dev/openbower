from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import SOURCE_CURSOR_MAX_LENGTH, SOURCE_KIND_MAX_LENGTH
from ..fields import SourceConfigField


class SignalSource(AccountScopedModel):
    """One thing that feeds a signal (a sheet of kind log): its kind's
    typed config, and the polling state a polled kind keeps (a webhook
    keeps none). A signal has at most one source per kind, removed with it
    by the log's delete (no cascades); the sheet doors refuse a log so
    nothing else can delete it from under its sources."""

    sheet_id = models.CharField(_("sheet id"), max_length=26)
    # The config's own kind, written from it by save(); a write that skips
    # save() (an update, a bulk insert) sets both. A column so the per-kind
    # constraint can hold it; readers pick a source by its config's class.
    kind = models.CharField(_("kind"), max_length=SOURCE_KIND_MAX_LENGTH, help_text=_("A registered source kind"))
    config = SourceConfigField(_("config"), help_text=_("The kind's config, signals.sources"))
    # Polled kinds only. Where the next read starts (the last item
    # appended), blank before the first.
    cursor = models.CharField(_("cursor"), max_length=SOURCE_CURSOR_MAX_LENGTH, blank=True, default="")
    # The poller's queue: when the source is next due, and who holds it
    # until when. Null is never due: a kind the poller never reads keeps
    # none, and a polled source gets its first from save(), so a row written
    # any other way waits rather than polls. A claim is a compare-and-set on
    # the lease; a lapsed lease is free again, so a dead poller needs no
    # reclaim.
    next_poll_at = models.DateTimeField(_("next poll at"), null=True, blank=True)
    leased_until = models.DateTimeField(_("leased until"), null=True, blank=True)
    leased_by = models.CharField(_("leased by"), max_length=128, blank=True, default="")
    # When the poller last read the source through, null before the first:
    # what a signal that keeps failing shows ("last read 3h ago").
    last_polled_at = models.DateTimeField(_("last polled at"), null=True, blank=True)
    # When the source stopped for good (a hosted listener the signals
    # service no longer has). Final: it leaves the due list, and the person
    # deletes the signal.
    gone_at = models.DateTimeField(_("gone at"), null=True, blank=True)

    class Meta:
        verbose_name = _("signal source")
        verbose_name_plural = _("signal sources")
        constraints = [
            # One source of each kind per signal; its index also serves every
            # read of a signal's sources by sheet_id alone.
            models.UniqueConstraint(fields=["sheet_id", "kind"], name="signals_source_sheet_kind_uniq"),
        ]
        indexes = [
            models.Index(fields=["account_id", "id"], name="signals_source_account_idx"),
            # The due list: live polled sources only, longest waiting first.
            models.Index(
                fields=["next_poll_at", "id"],
                name="signals_source_due_idx",
                condition=models.Q(gone_at__isnull=True, next_poll_at__isnull=False),
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        self.kind = self.config.kind
        if not self.config.POLLED:
            self.next_poll_at = None
        elif self._state.adding and self.next_poll_at is None:
            self.next_poll_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.kind} source -> {self.sheet_id}"

from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel


class LogFeed(AccountScopedModel):
    """What feeds a log (a sheet of kind log): the one listener whose
    matches become its rows, and whether arriving rows autofill. One per
    log, removed with it by the log's delete (no cascades), and the sheet
    doors refuse a log so nothing else can delete it from under its feed."""

    sheet_id = models.CharField(_("sheet id"), max_length=26)
    listener_id = models.CharField(_("listener id"), max_length=26)
    # The signal it logs, as the catalog names it: what chose the sheet's
    # base columns, and what reads each match into a row. Set at create.
    signal = models.CharField(_("signal"), max_length=32)
    autofill = models.BooleanField(_("autofill"), default=True)

    class Meta:
        verbose_name = _("log feed")
        verbose_name_plural = _("log feeds")
        # One feed per log.
        constraints = [models.UniqueConstraint(fields=["sheet_id"], name="signals_log_feed_sheet_uniq")]
        indexes = [models.Index(fields=["account_id", "id"], name="signals_log_feed_account_idx")]

    def __str__(self) -> str:
        return f"feed {self.listener_id} -> {self.sheet_id}"

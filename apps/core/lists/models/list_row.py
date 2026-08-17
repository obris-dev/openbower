from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import BaseModel


class ListRow(BaseModel):
    """One row of a sheet: free-form `data` (JSON keyed by the list's
    column keys) and nothing else. Rows hold no entity references; any
    company behavior interprets a chosen column's values at use time."""

    list_id = models.CharField(_("list id"), max_length=26)
    position = models.IntegerField(_("position"), help_text=_("1-based, dense; the display/paging order"))
    data = models.JSONField(_("data"), default=dict)

    class Meta:
        verbose_name = _("list row")
        verbose_name_plural = _("list rows")
        constraints = [
            models.UniqueConstraint(fields=["list_id", "position"], name="list_row_position_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.list_id}#{self.position}"

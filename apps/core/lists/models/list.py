from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel

from ..constants import LABEL_MAX_LENGTH, ORIGIN_MAX_LENGTH


class List(UserScopedModel):
    """A named SHEET with its own schema: `columns` is a JSON array of
    {key, label, type} in display order; rows carry data keyed by those
    columns. Content-agnostic on purpose: companies are one KIND of
    content, and company-ness is a use-time interpretation of a column
    (nothing here stores company references)."""

    folder_id = models.CharField(_("folder id"), max_length=26, blank=True, default="")
    label = models.CharField(_("label"), max_length=LABEL_MAX_LENGTH)
    columns = models.JSONField(_("columns"), default=list, help_text=_("[{key, label, type}] in display order"))
    origin = models.CharField(_("origin"), max_length=ORIGIN_MAX_LENGTH, help_text=_("ListOrigin"))
    origin_ref = models.CharField(
        _("origin ref"), max_length=64, blank=True, default="", help_text=_("e.g. the source run id")
    )
    row_count = models.IntegerField(_("row count"), default=0)

    class Meta:
        verbose_name = _("list")
        verbose_name_plural = _("lists")
        indexes = [models.Index(fields=["account_id", "id"], name="list_account_idx")]

    def __str__(self) -> str:
        return self.label

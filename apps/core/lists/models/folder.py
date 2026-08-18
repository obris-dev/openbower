from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import BaseModel

from ..constants import LABEL_MAX_LENGTH


class Folder(BaseModel):
    """A flat, account-scoped bucket for lists: the user's own taxonomy
    over their sheets, nothing more (no nesting, no behavior)."""

    account_id = models.CharField(_("account id"), max_length=26)
    user_id = models.CharField(_("user id"), max_length=26)
    label = models.CharField(_("label"), max_length=LABEL_MAX_LENGTH)

    class Meta:
        verbose_name = _("folder")
        verbose_name_plural = _("folders")
        indexes = [models.Index(fields=["account_id", "id"], name="folder_account_idx")]

    def __str__(self) -> str:
        return self.label

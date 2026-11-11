from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel

from ..constants import KIND_MAX_LENGTH, LABEL_MAX_LENGTH, ORIGIN_MAX_LENGTH, SheetKind
from ..fields import SheetColumnsField


class Sheet(UserScopedModel):
    """A named SHEET with its own schema: `columns` is the contract's
    SheetColumn values in display order (a JSON array of their dumps at
    rest, typed on both load and assignment by SheetColumnsField); rows
    carry data keyed by those columns. Content-agnostic on purpose:
    companies are one KIND of content, and company-ness is a use-time
    interpretation of a column (nothing here stores company
    references)."""

    # db_default as well: a process still on the code before this column
    # inserts without it, and the column must take the value then too.
    kind = models.CharField(
        _("kind"),
        max_length=KIND_MAX_LENGTH,
        default=SheetKind.STANDARD.value,
        db_default=SheetKind.STANDARD.value,
        help_text=_("SheetKind"),
    )
    folder_id = models.CharField(_("folder id"), max_length=26, blank=True, default="")
    label = models.CharField(_("label"), max_length=LABEL_MAX_LENGTH)
    columns = SheetColumnsField(
        _("columns"), default=list, help_text=_("The contract's SheetColumn dumps, in display order")
    )
    origin = models.CharField(_("origin"), max_length=ORIGIN_MAX_LENGTH, help_text=_("SheetOrigin"))
    origin_ref = models.CharField(
        _("origin ref"), max_length=64, blank=True, default="", help_text=_("e.g. the source run id")
    )
    row_count = models.IntegerField(_("row count"), default=0)

    class Meta:
        verbose_name = _("sheet")
        verbose_name_plural = _("sheets")
        # Every listing reads one kind of an account newest first (the
        # sheets index, folder counts, the logs list); account_id alone
        # is this index's prefix.
        indexes = [models.Index(fields=["account_id", "kind", "id"], name="sheet_account_kind_idx")]

    def __str__(self) -> str:
        return self.label

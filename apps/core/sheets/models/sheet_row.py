from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import BaseModel
from openbower_schema.sheets import EXTERNAL_ID_MAX_LENGTH

from ..constants import RANK_MAX_LENGTH


class SheetRow(BaseModel):
    """One row of a sheet: free-form `data` (JSON keyed by the sheet's
    column keys) and nothing else. Rows hold no entity references; any
    company behavior interprets a chosen column's values at use time.

    The row's IDENTITY is its id (a ULID, so ids are also insertion
    order); its ORDER is `rank`, a fractional key (openbower_kernel.ranks)
    that sorts lexically, so a move writes one row and never renumbers
    its neighbours. Row numbers are derived by whoever renders a page,
    never stored."""

    sheet_id = models.CharField(_("sheet id"), max_length=26)
    # The C collation, so the database compares ranks in the digits'
    # own byte order; a locale collation would sort `a` before `B` and
    # break the scheme.
    rank = models.CharField(_("rank"), max_length=RANK_MAX_LENGTH, db_collation="C")
    data = models.JSONField(_("data"), default=dict)
    # The sender's id for this row, blank when it sent none: a push that
    # names an id the sheet already holds skips that row. db_default as
    # well, for a rolling deploy.
    external_id = models.CharField(
        _("external id"), max_length=EXTERNAL_ID_MAX_LENGTH, blank=True, default="", db_default=""
    )

    class Meta:
        verbose_name = _("sheet row")
        verbose_name_plural = _("sheet rows")
        constraints = [
            models.UniqueConstraint(fields=["sheet_id", "rank"], name="sheet_row_rank_uniq"),
            # A CharField silently stores "" when a writer forgets the rank;
            # refused at the insert, as on a node and a run.
            models.CheckConstraint(condition=~models.Q(rank=""), name="sheet_row_rank_named"),
            # One row per sender id on a sheet; rows sent without one are
            # never compared.
            models.UniqueConstraint(
                fields=["sheet_id", "external_id"],
                condition=~models.Q(external_id=""),
                name="sheet_row_external_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sheet_id}@{self.rank}"

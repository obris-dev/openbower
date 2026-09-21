from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import BaseModel

from ..constants import RANK_MAX_LENGTH


class ListRow(BaseModel):
    """One row of a sheet: free-form `data` (JSON keyed by the list's
    column keys) and nothing else. Rows hold no entity references; any
    company behavior interprets a chosen column's values at use time.

    The row's IDENTITY is its id (a ULID, so ids are also insertion
    order); its ORDER is `rank`, a fractional key (openbower_kernel.ranks)
    that sorts lexically, so a move writes one row and never renumbers
    its neighbours. Row numbers are derived by whoever renders a page,
    never stored."""

    list_id = models.CharField(_("list id"), max_length=26)
    # The C collation, so the database compares ranks in the digits'
    # own byte order; a locale collation would sort `a` before `B` and
    # break the scheme.
    rank = models.CharField(_("rank"), max_length=RANK_MAX_LENGTH, db_collation="C")
    data = models.JSONField(_("data"), default=dict)

    class Meta:
        verbose_name = _("list row")
        verbose_name_plural = _("list rows")
        constraints = [
            models.UniqueConstraint(fields=["list_id", "rank"], name="list_row_rank_uniq"),
            # A CharField silently stores "" when a writer forgets the rank;
            # refused at the insert, as on a node and a run.
            models.CheckConstraint(condition=~models.Q(rank=""), name="list_row_rank_named"),
        ]

    def __str__(self) -> str:
        return f"{self.list_id}@{self.rank}"

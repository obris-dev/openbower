from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel


class Workflow(AccountScopedModel):
    """The per-sheet container the nodes hang off: one per list.

    Created by admission's node get-or-create the first time a sheet
    gains an AI column, never by list create, so a sheet with no AI
    column has no workflow. Deleted only by ListService.delete, in the
    list's own transaction: nodes and paths point at it by id, nothing
    cascades, and the owning service removes them in order. It carries
    only the list pointer today: no entry pointer (one path per node, so
    there is no single entry) and no flag, since nothing reads either
    yet."""

    list_id = models.CharField(_("list id"), max_length=26)

    class Meta:
        verbose_name = _("workflow")
        verbose_name_plural = _("workflows")
        constraints = [
            # One workflow per sheet; also the get-or-create key, and the
            # only access path anything reads today.
            models.UniqueConstraint(fields=["list_id"], name="workflow_list_uniq"),
        ]

    def __str__(self) -> str:
        return f"workflow of {self.list_id} ({self.id})"

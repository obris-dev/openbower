from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel


class NodePath(AccountScopedModel):
    """One path under a workflow: the sequential-scheduling unit.

    Nodes on one path run one after another; nodes on different paths
    are independent. Today every node gets its own path, because no
    dependency exists between runs yet; a join, where paths converge, is
    deferred, and adding one is a pure add. Deleted with its workflow by
    ListService.delete; nodes point at it by id."""

    workflow_id = models.CharField(_("workflow id"), max_length=26)

    class Meta:
        verbose_name = _("node path")
        verbose_name_plural = _("node paths")
        indexes = [
            # The delete's access path: a workflow's paths.
            models.Index(fields=["account_id", "workflow_id"], name="node_path_workflow_idx"),
        ]

    def __str__(self) -> str:
        return f"path of {self.workflow_id} ({self.id})"

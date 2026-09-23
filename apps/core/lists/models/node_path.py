from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel


class NodePath(AccountScopedModel):
    """One path under a workflow: the sequential-scheduling unit.

    Nodes on one path run one after another; nodes on different paths
    are independent. Every path STARTS with a marker node saying how it
    is fed: an entry (by nothing, so an arrival starts it) or a
    wait_until (by the paths it names, so its barrier does). The marker
    is what makes "which paths does an arrival start" one indexed read
    rather than a walk of the workflow's nodes, and nothing here stores
    it: it is the head node, and the workflow writer keeps it there.
    Deleted with its workflow by ListService.delete; nodes point at it
    by id."""

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

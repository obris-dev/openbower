from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import NODE_IDENTITY_MAX_LENGTH, NODE_KIND_MAX_LENGTH


class Node(AccountScopedModel):
    """One typed node: an action a run executes against a row.

    `kind` names a registered node kind (lists.nodes is a registry, so a
    new kind is a new module and no migration). `config` is that kind's
    TYPED config at rest, a NodeConfig instance dumped on write and
    parsed back by its class on read (services.workflows.config_of for
    the dynamic read), never touched raw, so this generic row never names a
    kind's field. `identity` is the kind-declared projection of the
    config that makes get-or-create indexable: the one writer derives it
    from the typed config, and nothing edits it.

    `rank` is the node's dense position on its path: a column_agent path
    holds one node at 0; a webhook column's path holds its wait node at
    0 and its webhook node at 1. Unique per path, so two nodes can never
    claim one slot.

    A sheet node points at its workflow and path. The bench node points
    at neither (workflow_id and path_id blank, exactly as a TEST fill's
    list_id is): the account's one sheetless column_agent node, the
    bookkeeping a test run's node_id points at, outliving every fill: an
    account-level singleton with no parent, the no-cascades rule's named
    exception, deleted by nothing.

    Durable: removing a column never removes a node, because ABANDONED
    runs (the consent record) keep pointing at it, and a node with no
    columns is inert. Deleted only by ListService.delete. The agent a
    config points at dangles after an agent delete exactly as the column
    did; that orphaning is deliberate (fill_admission/errors.py), so no
    hook."""

    workflow_id = models.CharField(_("workflow id"), max_length=26, blank=True, default="")
    path_id = models.CharField(_("path id"), max_length=26, blank=True, default="")
    kind = models.CharField(_("kind"), max_length=NODE_KIND_MAX_LENGTH)
    config = models.JSONField(_("config"), default=dict)
    identity = models.CharField(_("identity"), max_length=NODE_IDENTITY_MAX_LENGTH, blank=True, default="")
    rank = models.IntegerField(_("rank"), default=0)

    class Meta:
        verbose_name = _("node")
        verbose_name_plural = _("nodes")
        constraints = [
            # The get-or-create key: a sheet node is (account, workflow,
            # kind, identity); the bench node is (account, "", kind, "").
            models.UniqueConstraint(
                fields=["account_id", "workflow_id", "kind", "identity"],
                name="node_identity_uniq",
            ),
            # One node per slot on a path; the bench node has no path.
            models.UniqueConstraint(
                fields=["path_id", "rank"],
                condition=~models.Q(path_id=""),
                name="node_path_rank_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}#{self.identity} ({self.id})"

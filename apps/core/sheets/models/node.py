from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import NODE_IDENTITY_MAX_LENGTH, NODE_KIND_MAX_LENGTH, RANK_MAX_LENGTH


class Node(AccountScopedModel):
    """One typed node: an action a run executes against a row.

    `kind` names a registered node kind (lists.nodes is a registry, so a
    new kind is a new module and no migration). `config` is that kind's
    TYPED config at rest, a NodeConfig instance dumped on write and
    parsed back by its class on read (services.workflows.config_of for
    the dynamic read), never touched raw, so this generic row never names a
    kind's field. `identity` is the kind-declared projection of the
    config that makes get-or-create indexable: the one writer derives it
    from the typed config, and nothing edits it. A kind addressed by its
    path and rank alone (a wait node, a webhook node) declares NO
    identity and stores a blank one, which the identity key ignores.

    `rank` is the node's place on its path, a fractional key exactly as
    a row's (openbower_kernel.ranks): a fresh path's nodes read a0, a1,
    and so on, and moving one is a key between its two new neighbours,
    one write, no renumbering and no collision on the unique key. Every
    node has one, the preview node included (the first key, alone on its
    non-path): a CharField silently stores "" when a writer forgets it,
    and the check constraint below makes that a failed insert instead
    of a node sorted first forever.

    A sheet node points at its workflow and path. The preview node points
    at neither (workflow_id and path_id blank, exactly as a preview run's
    list_id is): the account's one sheetless column_agent node, the
    bookkeeping a preview run's node_id points at, outliving every fill: an
    account-level singleton with no parent, the no-cascades rule's named
    exception, deleted by nothing.

    An agent node is durable: removing its column never removes it,
    because ABANDONED runs (the consent record) keep pointing at it, and
    a node with no columns is inert; it goes only with its list. A
    webhook column's two nodes are its own and go with the column, its
    runs with them. The agent a config points at dangles after an
    agent delete exactly as the column did; that orphaning is deliberate
    (fill_admission/errors.py), so no hook."""

    workflow_id = models.CharField(_("workflow id"), max_length=26, blank=True, default="")
    path_id = models.CharField(_("path id"), max_length=26, blank=True, default="")
    kind = models.CharField(_("kind"), max_length=NODE_KIND_MAX_LENGTH)
    config = models.JSONField(_("config"), default=dict)
    identity = models.CharField(_("identity"), max_length=NODE_IDENTITY_MAX_LENGTH, blank=True, default="")
    rank = models.CharField(_("rank"), max_length=RANK_MAX_LENGTH, db_collation="C")

    class Meta:
        verbose_name = _("node")
        verbose_name_plural = _("nodes")
        # Every service read of nodes is by kind: a SHEET's markers (the
        # entry heads an arrival starts at, the wait naming a path) are
        # per workflow, the webhook nodes naming a destination are per
        # account. Each read takes its own index range and the JSON
        # condition then filters that handful in memory.
        indexes = [
            models.Index(fields=["account_id", "kind"], name="node_account_kind_idx"),
            models.Index(fields=["account_id", "workflow_id", "kind"], name="node_workflow_kind_idx"),
        ]
        constraints = [
            # The get-or-create key for kinds that declare an identity: a
            # sheet node is (account, workflow, kind, identity); the preview
            # node is (account, "", kind, preview). A blank identity is a
            # kind that is never looked up this way, and stays out.
            models.UniqueConstraint(
                fields=["account_id", "workflow_id", "kind", "identity"],
                condition=~models.Q(identity=""),
                name="node_identity_uniq",
            ),
            # One node per slot on a path; the preview node has no path.
            models.UniqueConstraint(
                fields=["path_id", "rank"],
                condition=~models.Q(path_id=""),
                name="node_path_rank_uniq",
            ),
            models.CheckConstraint(condition=~models.Q(rank=""), name="node_rank_named"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}#{self.identity} ({self.id})"

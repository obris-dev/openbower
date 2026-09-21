"""The workflow substrate's ONE writer: the workflow a sheet's nodes
hang off, the path each node sits on, and the node itself, created on
demand by admission and removed only with the list.

Every multi-step write here is its OWN transaction (a savepoint when a
caller such as admission or the list delete already holds one), so a
node never lands without its path and a workflow never loses only some
of its children, whoever calls. Get-or-create is idempotent by the
node's unique key, so it runs UNLOCKED: a racing second admission blocks
on the unique index until the winner's transaction ends, then re-reads
the winner's row; a refused admission rolls its node back with everything else. Node
BEFORE path, so a racing loser mints no orphan path.

The module-level readers are the ONE spelling of a node's at-rest
config (config_of), of "which columns does a node fill" (a run fills a
node's whole set), and of the node -> typed config hop (config_as, and
agent_id_of over it); every caller that needs any of the three reads it
here.

Path persistence is KIND-BLIND: a caller builds typed configs in
memory and hands them over in rank order; the writer creates the path
and stores each config at its rank as what the config says it is. What
a webhook column's path looks like is that column's service's
knowledge, not this module's.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from django.db import transaction
from django.db.models import QuerySet

from openbower_kernel.ranks import first_key, key_between, keys_between, respace_keys
from openbower_schema.lists import AiColumn

from ..constants import RANK_REBALANCE_LENGTH
from ..models import List, Node, NodePath, Workflow
from ..nodes.base import NodeConfig
from ..nodes.column_agent import ColumnAgent
from ..nodes.registry import parse_config
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook

logger = logging.getLogger(__name__)


class NodeNotFound(Exception):
    """Missing OR foreign node (cross-tenant reads as not-found)."""


class PathNotFound(Exception):
    """Missing OR foreign path (cross-tenant reads as not-found)."""


class WrongNodeKind(Exception):
    """A node reached through a path that expects another kind: a caller
    bug or corruption, never a user-facing refusal."""


class PathHeadFixed(Exception):
    """A path that starts with a barrier keeps it first: the barrier is
    what its other nodes stand behind, so it is not moved and nothing is
    moved ahead of it."""


def columns_by_node(target_list: List) -> dict[str, list[str]]:
    """Each node the sheet's columns bind to, mapped to the keys it
    fills, in column order (a node can own several: a multi-output
    agent is one node)."""
    mapping: dict[str, list[str]] = {}
    for column in target_list.columns:
        if isinstance(column, AiColumn):
            mapping.setdefault(column.node_id, []).append(column.key)
    return mapping


def columns_for_node(target_list: List, node_id: str) -> tuple[str, ...]:
    """The keys one node fills on this sheet; empty when none bind to
    it (a node whose last column was removed is inert, not gone)."""
    return tuple(columns_by_node(target_list).get(node_id, ()))


def config_of(node: Node) -> NodeConfig:
    """At-rest row -> the kind's typed config, the one DYNAMIC read: the
    kind comes off the row's own column. A reader that already knows the
    kind parses through its spec instead (agent_id_of below)."""
    return parse_config(node.kind, node.config)


def config_as[ConfigT: NodeConfig](node: Node, cls: type[ConfigT]) -> ConfigT:
    """A node's config as the kind a caller expects. Any other kind
    refuses HERE, at the hop, rather than wherever the caller next trips
    over a missing field."""
    if node.kind != cls.KIND:
        raise WrongNodeKind(f"node {node.id} is {node.kind!r}, expected {cls.KIND!r}")
    return cls.model_validate(node.config)


def agent_id_of(node: Node) -> str:
    """The ONE node -> agent hop: every fill lane runs column_agent nodes."""
    return config_as(node, ColumnAgent).agent_id


class WorkflowService:
    def __init__(self, *, account_id: str):
        self.account_id = account_id

    def ensure_workflow(self, target_list: List) -> Workflow:
        """The sheet's workflow, minted on first use. A list already
        owned by another account's workflow fails on the unique list
        key, which is the loud shape corruption should take."""
        workflow, _ = Workflow.objects.get_or_create(account_id=self.account_id, list_id=str(target_list.id))
        return workflow

    def get_or_create_column_agent_node(self, target_list: List, *, agent_id: str) -> Node:
        """The one node for this agent on this sheet: a second column
        from the same agent reuses it, so a run's grain (an agent's whole
        column set) and the node's agree. One transaction: a node that
        landed without its path would be found, never repaired, by every
        later get-or-create."""
        with transaction.atomic():
            workflow = self.ensure_workflow(target_list)
            config = ColumnAgent(agent_id=agent_id)
            node, created = Node.objects.get_or_create(
                account_id=self.account_id,
                workflow_id=str(workflow.id),
                kind=config.KIND,
                identity=config.identity(),
                # The node is alone on the path minted below, at the first key.
                defaults={"config": config.model_dump(), "rank": first_key()},
            )
            if created:
                path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
                node.path_id = str(path.id)
                node.save(update_fields=["path_id", "updated_at"])
        return node

    def get_or_create_bench_node(self) -> Node:
        """The account's one sheetless column_agent node, the node a
        TEST run points at. No workflow and no path: nothing schedules
        against it, and it outlives every fill. Alone on its non-path,
        it holds the first key, as any node holds one."""
        config = ColumnAgent()
        node, _ = Node.objects.get_or_create(
            account_id=self.account_id,
            workflow_id="",
            kind=config.KIND,
            identity=config.identity(),
            defaults={"config": config.model_dump(), "rank": first_key()},
        )
        return node

    def get_node(self, node_id: str) -> Node:
        try:
            return Node.objects.get(id=node_id, account_id=self.account_id)
        except Node.DoesNotExist as e:
            raise NodeNotFound(node_id) from e

    def create_path(self, target_list: List, nodes: Sequence[NodeConfig]) -> tuple[NodePath, list[Node]]:
        """A new path on the sheet's workflow holding `nodes` in order,
        each at a fresh rank key. One transaction, path FIRST: unlike
        get-or-create there is no identity to race on, so a failed node
        write rolls the path back with it."""
        with transaction.atomic():
            workflow = self.ensure_workflow(target_list)
            path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
            ranks = keys_between(None, None, len(nodes))
            stored = [
                self._store(config, rank=rank, workflow_id=str(workflow.id), path_id=str(path.id))
                for rank, config in zip(ranks, nodes, strict=True)
            ]
        return path, stored

    def move_node(self, path_id: str, node_id: str, *, after_id: str | None) -> Node:
        """Put the node right after `after_id` (None: first on the path).
        ONE write, on the moved node: a key between its two new
        neighbours, and none at all when it already sits there. Under
        the path's lock, held for the reads and the one write only, so
        two moves into the same gap cannot compute the same key. A
        path that starts with a barrier keeps it first (PathHeadFixed):
        its other nodes stand behind it. A key past
        RANK_REBALANCE_LENGTH (moves into one gap, many times over)
        re-spaces the whole path here and now, in the new order: a
        path holds a handful of nodes and nothing pages over one, so
        the write is small and a user never waits on a job or meets a
        refusal. Logged, since it should be rare."""
        if after_id == node_id:
            # Dropped on itself: nothing to read, so no lock to take.
            return self.get_node(node_id)
        with transaction.atomic():
            if self._locked_path(path_id) is None:
                raise PathNotFound(path_id)
            on_path = Node.objects.filter(account_id=self.account_id, path_id=path_id)
            node = on_path.filter(id=node_id).first()
            if node is None:
                raise NodeNotFound(node_id)
            others = on_path.exclude(id=node_id)
            if after_id is None:
                before_rank = None
                nxt = others.order_by("rank").only("rank").first()
            else:
                before = others.filter(id=after_id).only("rank").first()
                if before is None:
                    raise NodeNotFound(after_id)
                before_rank = before.rank
                nxt = others.filter(rank__gt=before.rank).order_by("rank").only("rank").first()
            # Ranks are unique per path, so "already between" is strict.
            above = before_rank is None or before_rank < node.rank
            below = nxt is None or node.rank < nxt.rank
            if above and below:
                return node
            # Judged after the no-op: a barrier dropped where it already
            # sits is nothing, a barrier moved or a node put ahead of it
            # is refused.
            head = on_path.order_by("rank").only("id", "kind").first()
            if head.kind == WaitUntil.KIND and (str(head.id) == node_id or after_id is None):
                raise PathHeadFixed(path_id)
            key = key_between(before_rank, nxt.rank if nxt is not None else None)
            if len(key) <= RANK_REBALANCE_LENGTH:
                node.rank = key
                node.save(update_fields=["rank", "updated_at"])
                return node
            logger.warning("path %s: the next key would be %d characters, re-spacing the path", path_id, len(key))
            # Ids and ranks only: every node takes a fresh key, but none
            # of them needs its config loaded for that.
            ordered = list(others.order_by("rank").only("id", "rank"))
            at = 0 if after_id is None else 1 + next(i for i, n in enumerate(ordered) if str(n.id) == after_id)
            ordered.insert(at, node)
            self._respace(ordered)
        return node

    def _locked_path(self, path_id: str) -> NodePath | None:
        """The path row FOR UPDATE, the one lock every writer of a
        path's nodes takes first (a move, a config replace, a delete),
        so no two of them interleave and no pair can wait on each other
        the other way round. Taken inside the caller's transaction and
        held only as long as it."""
        return NodePath.objects.select_for_update().filter(id=path_id, account_id=self.account_id).first()

    @staticmethod
    def _respace(nodes: list[Node]) -> None:
        """Fresh keys for `nodes` in the order given, disjoint from the
        keys they hold (respace_keys says why), written in one update."""
        fresh = respace_keys([n.rank for n in nodes])
        for n, rank in zip(nodes, fresh, strict=True):
            n.rank = rank
        Node.objects.bulk_update(nodes, ["rank"])

    def _store(self, config: NodeConfig, *, rank: str, workflow_id: str, path_id: str) -> Node:
        return Node.objects.create(
            account_id=self.account_id,
            workflow_id=workflow_id,
            path_id=path_id,
            kind=config.KIND,
            identity=config.identity(),
            config=config.model_dump(),
            rank=rank,
        )

    def save_node(self, node: Node, config: NodeConfig) -> Node:
        """A new config on an existing node. A config of another kind
        is a caller bug, refused at the hop."""
        if node.kind != config.KIND:
            raise WrongNodeKind(f"node {node.id} is {node.kind!r}, cannot hold a {config.KIND!r} config")
        node.identity = config.identity()
        node.config = config.model_dump()
        node.save(update_fields=["identity", "config", "updated_at"])
        return node

    def replace_path_nodes(self, path_id: str, nodes: Sequence[NodeConfig]) -> list[Node]:
        """New configs for a path's nodes, rank by rank, in one
        transaction under the path's lock (a move committed between the
        read and the saves would otherwise land each config on the
        wrong node). The path keeps its shape: a config count or kind
        that differs from what is there is a caller bug."""
        with transaction.atomic():
            self._locked_path(path_id)
            existing = self.nodes_on_path(path_id)
            if len(existing) != len(nodes):
                raise WrongNodeKind(f"path {path_id} holds {len(existing)} nodes, got {len(nodes)} configs")
            return [self.save_node(node, config) for node, config in zip(existing, nodes, strict=True)]

    def delete_path(self, path_id: str) -> None:
        """A path and its nodes, one transaction, the path's lock first
        (the order every writer takes them in). Only for a path whose
        nodes no run points at (a webhook column's); an agent path is
        durable, see Node."""
        with transaction.atomic():
            self._locked_path(path_id)
            Node.objects.filter(account_id=self.account_id, path_id=path_id).delete()
            NodePath.objects.filter(account_id=self.account_id, id=path_id).delete()

    def nodes_by_id(self, node_ids: Sequence[str]) -> list[Node]:
        """The account's nodes among `node_ids`, one read; a gone id is
        simply absent."""
        if not node_ids:
            return []
        return list(Node.objects.filter(account_id=self.account_id, id__in=list(node_ids)))

    def nodes_on_path(self, path_id: str) -> list[Node]:
        return list(Node.objects.filter(account_id=self.account_id, path_id=path_id).order_by("rank"))

    # The two config QUERIES (as against parses), named here so no other
    # module spells a JSON lookup against this table.

    def wait_ahead_of(self, node: Node) -> WaitUntil:
        """The barrier a node stands behind: its path's first node, parsed
        as a wait node. Raises NodeNotFound when the path is gone or
        does not start with one (the shape every webhook column's path
        has; a node with no path has no barrier)."""
        path_nodes = self.nodes_on_path(node.path_id) if node.path_id else []
        if not path_nodes or path_nodes[0].kind != WaitUntil.KIND:
            raise NodeNotFound(node.path_id)
        return config_as(path_nodes[0], WaitUntil)

    def nodes_ending(self, path_ids: Sequence[str]) -> list[Node]:
        """The account's nodes on the given paths (the agent nodes the
        paths a barrier names end in)."""
        if not path_ids:
            return []
        return list(Node.objects.filter(account_id=self.account_id, path_id__in=list(path_ids)))

    def wait_nodes_naming(self, path_id: str) -> QuerySet[Node]:
        """The account's wait nodes whose inbound set names a path: what
        makes an AI column's delete refuse."""
        return Node.objects.filter(
            account_id=self.account_id, kind=WaitUntil.KIND, config__inbound_path_ids__contains=[path_id]
        )

    def webhook_nodes_for(self, destination_id: str) -> QuerySet[Node]:
        """The account's webhook nodes sending to a destination: what
        makes its delete refuse, and its usage count."""
        return Node.objects.filter(account_id=self.account_id, kind=Webhook.KIND, config__destination_id=destination_id)

    def path_of_column(self, target_list: List, key: str) -> str:
        """The path an AI column's node sits on, the id a wait node
        names; raises NodeNotFound for a column with no node."""
        for column in target_list.columns:
            if column.key == key and isinstance(column, AiColumn):
                return self.get_node(column.node_id).path_id
        raise NodeNotFound(key)

    def delete_for_list(self, list_id: str) -> None:
        """Called from ListService.delete AFTER the runs are purged (they
        point at nodes): nodes, then paths, then the workflow, as one
        transaction. No-op for a sheet that never gained a node."""
        with transaction.atomic():
            workflow = Workflow.objects.filter(account_id=self.account_id, list_id=list_id).first()
            if workflow is None:
                return
            workflow_id = str(workflow.id)
            # The paths' locks first, the order every writer takes them in.
            list(NodePath.objects.select_for_update().filter(account_id=self.account_id, workflow_id=workflow_id))
            Node.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            NodePath.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            workflow.delete()

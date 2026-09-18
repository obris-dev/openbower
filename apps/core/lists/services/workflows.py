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

from collections.abc import Sequence

from django.db import transaction
from django.db.models import Count, QuerySet

from ..models import List, Node, NodePath, Workflow
from ..nodes.base import NodeConfig
from ..nodes.column_agent import ColumnAgent
from ..nodes.registry import parse_config


class NodeNotFound(Exception):
    """Missing OR foreign node (cross-tenant reads as not-found)."""


class WrongNodeKind(Exception):
    """A node reached through a path that expects another kind: a caller
    bug or corruption, never a user-facing refusal."""


def columns_by_node(target_list: List) -> dict[str, list[str]]:
    """Each node the sheet's columns bind to, mapped to the keys it
    fills, in column order (a node can own several: a multi-output
    agent is one node)."""
    mapping: dict[str, list[str]] = {}
    for column in target_list.columns:
        if column.fill is not None:
            mapping.setdefault(column.fill.node_id, []).append(column.key)
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
                defaults={"config": config.model_dump()},
            )
            if created:
                path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
                node.path_id = str(path.id)
                node.save(update_fields=["path_id", "updated_at"])
        return node

    def get_or_create_bench_node(self) -> Node:
        """The account's one sheetless column_agent node, the node a
        TEST run points at. No workflow and no path: nothing schedules
        against it, and it outlives every fill."""
        config = ColumnAgent()
        node, _ = Node.objects.get_or_create(
            account_id=self.account_id,
            workflow_id="",
            kind=config.KIND,
            identity=config.identity(),
            defaults={"config": config.model_dump()},
        )
        return node

    def get_node(self, node_id: str) -> Node:
        try:
            return Node.objects.get(id=node_id, account_id=self.account_id)
        except Node.DoesNotExist as e:
            raise NodeNotFound(node_id) from e

    def create_path(self, target_list: List, nodes: Sequence[NodeConfig]) -> tuple[NodePath, list[Node]]:
        """A new path on the sheet's workflow holding `nodes` in order,
        rank = position. One transaction, path FIRST: unlike
        get-or-create there is no identity to race on, so a failed node
        write rolls the path back with it."""
        with transaction.atomic():
            workflow = self.ensure_workflow(target_list)
            path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
            stored = [
                self._store(config, rank=rank, workflow_id=str(workflow.id), path_id=str(path.id))
                for rank, config in enumerate(nodes)
            ]
        return path, stored

    def _store(self, config: NodeConfig, *, rank: int, workflow_id: str, path_id: str) -> Node:
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
        transaction. The path keeps its shape: a config count or kind
        that differs from what is there is a caller bug."""
        with transaction.atomic():
            existing = self.nodes_on_path(path_id)
            if len(existing) != len(nodes):
                raise WrongNodeKind(f"path {path_id} holds {len(existing)} nodes, got {len(nodes)} configs")
            return [self.save_node(node, config) for node, config in zip(existing, nodes, strict=True)]

    def delete_path(self, path_id: str) -> None:
        """A path and its nodes, one transaction. Only for a path whose
        nodes no run points at (a webhook column's); an agent path is
        durable, see Node."""
        with transaction.atomic():
            Node.objects.filter(account_id=self.account_id, path_id=path_id).delete()
            NodePath.objects.filter(account_id=self.account_id, id=path_id).delete()

    def nodes_on_path(self, path_id: str) -> list[Node]:
        return list(Node.objects.filter(account_id=self.account_id, path_id=path_id).order_by("rank"))

    def nodes_of_kind(self, kind: str, **config_filters: object) -> QuerySet[Node]:
        """The account's nodes of one kind, narrowed by JSON lookups on
        the config (`config__destination_id=...`,
        `config__inbound_path_ids__contains=[...]`): the ONE place a
        config is queried rather than parsed."""
        return Node.objects.filter(account_id=self.account_id, kind=kind, **config_filters)

    def node_counts_by(self, kind: str, config_field: str) -> dict[str, int]:
        """How many nodes of `kind` name each value of one config field
        (a roster's per-destination counts), one grouped query."""
        lookup = f"config__{config_field}"
        rows = self.nodes_of_kind(kind).values_list(lookup).annotate(n=Count("id"))
        return {str(value): n for value, n in rows}

    def path_of_column(self, target_list: List, key: str) -> str:
        """The path an AI column's node sits on, the id a wait node
        names; raises NodeNotFound for a column with no node."""
        for column in target_list.columns:
            if column.key == key and column.fill is not None:
                return self.get_node(column.fill.node_id).path_id
        raise NodeNotFound(key)

    def delete_for_list(self, list_id: str) -> None:
        """Called from ListService.delete AFTER the runs are purged (they
        point at nodes): nodes, then paths, then the workflow, as one
        transaction. No-op for a sheet that never gained an AI column."""
        with transaction.atomic():
            workflow = Workflow.objects.filter(account_id=self.account_id, list_id=list_id).first()
            if workflow is None:
                return
            workflow_id = str(workflow.id)
            Node.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            NodePath.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            workflow.delete()

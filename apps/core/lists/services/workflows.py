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
node's whole set), and of the node -> agent hop; every caller that needs
any of the three reads it here.
"""

from __future__ import annotations

from django.db import transaction

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
        fill = column.get("fill")
        if fill and fill.get("node_id"):
            mapping.setdefault(fill["node_id"], []).append(column["key"])
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


def agent_id_of(node: Node) -> str:
    """The ONE node -> agent hop. Every lane runs column_agent nodes
    today, so any other kind refuses HERE, at the hop, rather than
    wherever the caller next trips over a missing agent."""
    if node.kind != ColumnAgent.KIND:
        raise WrongNodeKind(f"node {node.id} is {node.kind!r}, expected {ColumnAgent.KIND!r}")
    return ColumnAgent.model_validate(node.config).agent_id


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

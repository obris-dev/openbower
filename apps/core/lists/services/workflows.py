"""The workflow substrate's ONE writer: the workflow a sheet's nodes
hang off, the path each node sits on, and the node itself, created on
demand by the AI column create and removed only with the list.

Every multi-step write here is its OWN transaction (a savepoint when a
caller such as the column create or the list delete already holds one),
so a node never lands without its path and a workflow never loses only
some of its children, whoever calls. Get-or-create is idempotent by the
node's unique key, so it takes no lock of its own: a racing second
create blocks on the unique index until the winner's transaction ends,
then re-reads the winner's row; a create that fails after the node is
minted rolls it back with everything else (a column refusal is judged
before it). Node BEFORE path, so a racing loser mints no
orphan path.

The module-level readers are the ONE spelling of a node's at-rest
config (config_of), of "which columns does a node fill" (columns_for_node,
the judgement's set) and "which of them does a run write" (written_columns,
every lane's set), and of the node -> typed config hop (config_as, and
agent_id_of over it); every caller that needs any of them reads it
here.

Path persistence is KIND-BLIND: a caller builds typed configs in
memory and hands them over in rank order; the writer creates the path
and stores each config at its rank as what the config says it is. What
a webhook column's path looks like is that column's service's
knowledge, not this module's.

The ONE invariant this module holds: every path's head is exactly one
MARKER node saying how the path is fed, an entry (by nothing) or a
wait_until (by the paths it names). An agent column's path is minted
[entry, agent]; a path built from configs is refused unless its head is
a marker; no move displaces a head marker or puts a node ahead of one.
That is what lets a reader ask which paths an arrival starts with one
indexed read of the entry markers instead of a walk of the workflow.

The vocabulary: a path's HEAD is its marker, never anything else. Every
other node is an ACTION (it runs; a marker never does). An ENTRY ACTION
is the action right behind an entry marker: what an arriving row
starts, and the only place a user's fill may start.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import NamedTuple

from django.db import transaction
from django.db.models import QuerySet, Subquery

from openbower_kernel.ranks import first_key, key_between, keys_between, respace_keys
from openbower_schema.agents import AgentConfig
from openbower_schema.lists import WorkflowColumn

from ..constants import RANK_REBALANCE_LENGTH
from ..models import List, Node, NodePath, Workflow
from ..nodes.base import NodeConfig
from ..nodes.column_agent import ColumnAgent
from ..nodes.entry import Entry
from ..nodes.registry import ENTRY, HEAD_OF_PATH_MARKERS, parse_config
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook

logger = logging.getLogger(__name__)


class AgentColumnUse(NamedTuple):
    """One sheet with columns an agent fills."""

    list_id: str
    label: str
    column_keys: tuple[str, ...]


class NodeNotFound(Exception):
    """Missing OR foreign node (cross-tenant reads as not-found)."""


class PathNotFound(Exception):
    """Missing OR foreign path (cross-tenant reads as not-found)."""


class WrongNodeKind(Exception):
    """A node reached through a path that expects another kind: a caller
    bug or corruption, never a user-facing refusal."""


class PathHeadFixed(Exception):
    """A path keeps the marker it starts with first: the marker is what
    says the path is entered or waited for, and what its other nodes
    stand behind, so it is not moved and nothing is moved ahead of
    it."""


def columns_by_node(target_list: List) -> dict[str, list[str]]:
    """Each node the sheet's columns bind to, mapped to the keys it
    fills, in column order, whatever the node's kind (a node can own
    several: a multi-output agent is one node)."""
    mapping: dict[str, list[str]] = {}
    for column in target_list.columns:
        if isinstance(column, WorkflowColumn):
            mapping.setdefault(column.node_id, []).append(column.key)
    return mapping


def columns_for_node(target_list: List, node_id: str) -> tuple[str, ...]:
    """The keys one node fills on this sheet; empty when none bind to
    it (a node whose last column was removed is inert, not gone)."""
    return tuple(columns_by_node(target_list).get(node_id, ()))


def written_columns(target_list: List, node_id: str, config: AgentConfig) -> tuple[str, ...]:
    """The keys one agent node WRITES on this sheet: its columns that
    one of the agent's current outputs backs, in sheet order. The one
    spelling for every lane (a fill's consent and an arrival's run),
    so the two agree on what a run lands. The output set is fixed while
    the columns exist (an agent save refuses a change), so this is
    normally every column of the node; a save that raced the create
    can leave a column no output backs, and that column is written by
    no lane (a run has nothing to say for it, and a diagnosis it never
    produced must not land there)."""
    outputs = {output.key for output in config.outputs}
    return tuple(key for key in columns_for_node(target_list, node_id) if key in outputs)


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
        column set) and the node's agree. The path minted below reads
        [entry, agent]: nothing feeds it, so it heads with the entry
        marker and the agent stands behind it. One transaction: a node
        that landed without its path, or a path without its marker,
        would be found, never repaired, by every later get-or-create."""
        entry_rank, agent_rank = keys_between(None, None, 2)
        with transaction.atomic():
            workflow = self.ensure_workflow(target_list)
            config = ColumnAgent(agent_id=agent_id)
            node, created = Node.objects.get_or_create(
                account_id=self.account_id,
                workflow_id=str(workflow.id),
                kind=config.KIND,
                identity=config.identity(),
                # Second on the path minted below, behind its marker.
                defaults={"config": config.model_dump(), "rank": agent_rank},
            )
            if created:
                path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
                self._store(Entry(), rank=entry_rank, workflow_id=str(workflow.id), path_id=str(path.id))
                node.path_id = str(path.id)
                node.save(update_fields=["path_id", "updated_at"])
        return node

    def get_or_create_preview_node(self) -> Node:
        """The account's one sheetless column_agent node, the node a
        preview run points at. No workflow and no path: nothing schedules
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
        each at a fresh rank key. EXACTLY ONE head marker, at the head:
        a path says what starts it, one that says nothing is reachable
        by neither reaction, and one that says it twice is read as two
        paths whose second head stands behind the first's work. One
        transaction, path FIRST: unlike get-or-create there is no
        identity to race on, so a failed node write rolls the path back
        with it."""
        head = nodes[0].KIND if nodes else "nothing"
        if head not in HEAD_OF_PATH_MARKERS:
            raise WrongNodeKind(
                f"a path starts with a head marker ({', '.join(sorted(HEAD_OF_PATH_MARKERS))}), not {head!r}"
            )
        # The other half of "exactly one": a head marker BEHIND work is
        # read as a head by everything that looks for one, so the paths
        # it would start are the ones standing behind it, barrier and
        # all. Only this set is refused here, so a marker kind that ends
        # a path stays expressible.
        behind = sorted({config.KIND for config in nodes[1:] if config.KIND in HEAD_OF_PATH_MARKERS})
        if behind:
            raise WrongNodeKind(f"a head marker only ever heads its path, so {behind} cannot stand behind one")
        with transaction.atomic():
            workflow = self.ensure_workflow(target_list)
            path = NodePath.objects.create(account_id=self.account_id, workflow_id=str(workflow.id))
            ranks = keys_between(None, None, len(nodes))
            stored = [
                self._store(config, rank=rank, workflow_id=str(workflow.id), path_id=str(path.id))
                for rank, config in zip(ranks, nodes, strict=True)
            ]
        return path, stored

    def move_node(self, path_id: str, node_id: str, *, after_id: str) -> Node:
        """Put the node right after `after_id`, which every move
        names: position zero belongs to the path's head marker and no
        move takes it, so THE TOP OF THE WORK is the marker's own id
        and "first on the path" is not a destination a caller can ask
        for. A path that one day heads with work (a branch naming it
        from its parent's tail) would need that meaning back, and it
        would mean genuinely first, with no marker to stand behind.
        ONE write, on the moved node: a key between its two new
        neighbours, and none at all when it already sits there. Under
        the path's lock, held for the reads and the one write only, so
        two moves into the same gap cannot compute the same key. A
        path keeps the marker it starts with first (PathHeadFixed):
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
            before = others.filter(id=after_id).only("rank").first()
            if before is None:
                raise NodeNotFound(after_id)
            nxt = others.filter(rank__gt=before.rank).order_by("rank").only("rank").first()
            # Ranks are unique per path, so "already between" is strict.
            above = before.rank < node.rank
            below = nxt is None or node.rank < nxt.rank
            if above and below:
                return node
            # Judged after the no-op: a marker dropped where it already
            # sits is nothing, a marker moved or a node put ahead of it
            # is refused.
            head = on_path.order_by("rank").only("id", "kind").first()
            if head.kind in HEAD_OF_PATH_MARKERS and str(head.id) == node_id:
                raise PathHeadFixed(path_id)
            key = key_between(before.rank, nxt.rank if nxt is not None else None)
            if len(key) <= RANK_REBALANCE_LENGTH:
                node.rank = key
                node.save(update_fields=["rank", "updated_at"])
                return node
            logger.warning("path %s: the next key would be %d characters, re-spacing the path", path_id, len(key))
            # Ids and ranks only: every node takes a fresh key, but none
            # of them needs its config loaded for that.
            ordered = list(others.order_by("rank").only("id", "rank"))
            at = 1 + next(i for i, n in enumerate(ordered) if str(n.id) == after_id)
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
        """The LAST node of each of the given paths (what a barrier
        naming the path waits on), by rank; a path with no node ends in
        nothing and drops out. A path whose only node is its marker
        ends in the marker, which no column names, so a barrier over it
        waits on nothing."""
        if not path_ids:
            return []
        last_by_path: dict[str, Node] = {}
        for node in Node.objects.filter(account_id=self.account_id, path_id__in=list(path_ids)).order_by("rank"):
            last_by_path[node.path_id] = node
        return list(last_by_path.values())

    def node_after(self, path_id: str, rank: str) -> Node | None:
        """The node right after `rank` on a path (the advance's next
        step), or None at the path's end. Ranks compare in the column's
        C collation, so the database orders them."""
        return Node.objects.filter(account_id=self.account_id, path_id=path_id, rank__gt=rank).order_by("rank").first()

    def marker_and_first_action(self, node_id: str) -> tuple[Node, Node] | None:
        """The first two nodes of the path `node_id` stands on: its head
        marker and the first action behind it, in ONE read (a two-row
        range of the (path, rank) key under a subquery for the path).
        None for a node with no path, or a path holding its marker
        alone."""
        on_path = Node.objects.filter(account_id=self.account_id, id=node_id).exclude(path_id="").values("path_id")
        path_id = Subquery(on_path[:1])
        first_two = Node.objects.filter(account_id=self.account_id, path_id=path_id).order_by("rank")[:2]
        nodes = list(first_two)
        if len(nodes) < 2:
            return None
        return nodes[0], nodes[1]

    def entry_action_ids(self, list_id: str) -> list[str]:
        """The sheet's entry actions: each the action right behind an
        ENTRY marker, what an arrival starts and where a user's fill may
        start. ONE read: every node on the sheet's entry-headed paths, in
        path order, so the second of each path is its entry action (the
        first is its head marker)."""
        workflow = Workflow.objects.filter(account_id=self.account_id, list_id=list_id).values("id")
        entry_paths = Node.objects.filter(account_id=self.account_id, kind=ENTRY, workflow_id__in=workflow).values(
            "path_id"
        )
        on_entry_paths = (
            Node.objects.filter(account_id=self.account_id, path_id__in=entry_paths)
            .order_by("path_id", "rank")
            .values_list("path_id", "id")
        )
        actions: list[str] = []
        seen_on_path: dict[str, int] = {}
        for path_id, node_id in on_entry_paths:
            position = seen_on_path.get(path_id, 0)
            seen_on_path[path_id] = position + 1
            if position == 1:
                actions.append(str(node_id))
        return actions

    def agent_column_uses(self, agent_id: str) -> list[AgentColumnUse]:
        """Every sheet with a column the agent fills: its column-agent
        nodes (at most one per sheet, the get-or-create identity), each
        with the sheet's columns bound to it. A node whose columns were
        all deleted uses nothing. Three reads, asked on an agent save."""
        config = ColumnAgent(agent_id=agent_id)
        node_rows = Node.objects.filter(
            account_id=self.account_id, kind=config.KIND, identity=config.identity()
        ).values_list("workflow_id", "id")
        node_by_workflow = {str(workflow_id): str(node_id) for workflow_id, node_id in node_rows}
        if not node_by_workflow:
            return []
        workflow_rows = Workflow.objects.filter(account_id=self.account_id, id__in=list(node_by_workflow)).values_list(
            "list_id", "id"
        )
        workflow_by_list = {str(list_id): str(workflow_id) for list_id, workflow_id in workflow_rows}
        uses: list[AgentColumnUse] = []
        for target in List.objects.filter(account_id=self.account_id, id__in=list(workflow_by_list)).order_by("id"):
            node_id = node_by_workflow[workflow_by_list[str(target.id)]]
            keys = columns_for_node(target, node_id)
            if keys:
                uses.append(AgentColumnUse(list_id=str(target.id), label=target.label, column_keys=keys))
        return uses

    def workflow_of(self, list_id: str) -> Workflow | None:
        """The sheet's workflow, or None for a sheet that never gained a
        node. A READER: `ensure_workflow` is the one that mints."""
        return Workflow.objects.filter(account_id=self.account_id, list_id=list_id).first()

    def iter_entry_nodes(self, workflow_id: str) -> Iterator[Node]:
        """The workflow's ENTRY markers, the heads of the paths nothing
        feeds: what an arrival starts. LAZY and single-pass (the iter_
        rule), one range of the (account, workflow, kind) index, so a
        sheet's entries are read one at a time however many it has."""
        yield from (
            Node.objects.filter(account_id=self.account_id, workflow_id=workflow_id, kind=ENTRY)
            .order_by("id")
            .iterator()
        )

    def waits_on(self, workflow_id: str, path_id: str) -> QuerySet[Node]:
        """The barriers over a path: this WORKFLOW's wait nodes carrying
        it in their inbound set. Scoped to the workflow because a path
        is only ever fed from its own sheet, so another sheet's barrier
        holding the same id is never this path's. What the advance's
        second rule follows, and what makes an AI column's delete
        refuse. The index takes (account, workflow, kind) and the JSON
        condition filters that handful in memory."""
        return Node.objects.filter(
            account_id=self.account_id,
            workflow_id=workflow_id,
            kind=WaitUntil.KIND,
            config__inbound_path_ids__contains=[path_id],
        )

    def webhook_nodes_for(self, destination_id: str) -> QuerySet[Node]:
        """The account's webhook nodes sending to a destination: what
        makes its delete refuse, and its usage count."""
        return Node.objects.filter(account_id=self.account_id, kind=Webhook.KIND, config__destination_id=destination_id)

    def delete_for_list(self, list_id: str) -> None:
        """Called from ListService.delete AFTER the runs are purged (they
        point at nodes): nodes, then paths, then the workflow, as one
        transaction. No-op for a sheet that never gained a node."""
        with transaction.atomic():
            workflow = self.workflow_of(list_id)
            if workflow is None:
                return
            workflow_id = str(workflow.id)
            # The paths' locks first, the order every writer takes them in.
            list(NodePath.objects.select_for_update().filter(account_id=self.account_id, workflow_id=workflow_id))
            Node.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            NodePath.objects.filter(account_id=self.account_id, workflow_id=workflow_id).delete()
            workflow.delete()

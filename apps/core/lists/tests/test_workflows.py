"""The workflow substrate: one workflow per sheet, one path per node,
one node per (agent, sheet), minted by admission on demand and removed
only with the list; the preview node is the account's one sheetless
node. Real DB and services; model_for is patched by the admission
fixture, and the rollback tests fail NodePath's writer to prove the
transaction.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from openbower_schema.agents import AgentOutput
from webhooks.services import WebhookDestinationService

from ..models import Node, NodePath, NodeRun, Workflow
from ..nodes.base import NodeConfig
from ..nodes.column_agent import PREVIEW_IDENTITY, ColumnAgent
from ..nodes.entry import Entry
from ..nodes.registry import HEAD_OF_PATH_MARKERS
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from ..services.columns import ColumnService
from ..services.fill_admission import NoEligibleRows
from ..services.webhook_columns import WebhookColumnService
from ..services.workflows import (
    NodeNotFound,
    PathHeadFixed,
    PathNotFound,
    WorkflowService,
    WrongNodeKind,
    agent_id_of,
    columns_by_node,
    columns_for_node,
    config_as,
)
from .fill_helpers import consent_of, settle_all
from .test_fill_admission import ACCOUNT, USER, AdmissionTestCase, quick_config, tick_jobs


class NodeGetOrCreateTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())

    def test_get_or_create_is_idempotent_and_points_the_node_at_its_path(self) -> None:
        first = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        second = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertEqual(first.id, second.id)
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (2, 1, 1))
        # The path reads [entry, agent]: nothing feeds it, so its head is
        # the entry marker and the agent stands behind it.
        self.assertEqual(
            [(n.kind, n.rank) for n in self.workflows.nodes_on_path(first.path_id)],
            [(Entry.KIND, "a0"), (ColumnAgent.KIND, "a1")],
        )
        workflow = Workflow.objects.get(list_id=str(self.sheet.id))
        path = NodePath.objects.get()
        self.assertEqual(
            (first.workflow_id, first.path_id, path.workflow_id), (str(workflow.id), str(path.id), str(workflow.id))
        )
        # The typed config at rest, and the identity the unique key indexes.
        self.assertEqual((first.kind, first.identity), ("column_agent", str(self.agent.id)))
        self.assertEqual(first.config, {"agent_id": str(self.agent.id)})
        self.assertEqual(agent_id_of(first), str(self.agent.id))

    def test_a_node_never_lands_without_its_path(self) -> None:
        # The path create fails after the node is written: the node must
        # roll back with it, or every later get-or-create would find a
        # pathless node and never repair it. FAILS if the method's own
        # transaction is removed (the node survives the failed path).
        with (
            patch("lists.services.workflows.NodePath.objects.create", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (0, 0))
        # The marker write fails after the node and the path: all three
        # roll back, or a path would head with its agent forever.
        with (
            patch("lists.services.workflows.Node.objects.create", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (0, 0))
        # And the next call mints the three cleanly.
        node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertEqual(node.path_id, str(NodePath.objects.get().id))
        self.assertEqual([n.kind for n in self.workflows.nodes_on_path(node.path_id)], [Entry.KIND, ColumnAgent.KIND])

    def test_the_preview_node_is_one_per_account_with_no_workflow_or_path(self) -> None:
        first = self.workflows.get_or_create_preview_node()
        second = self.workflows.get_or_create_preview_node()
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            (first.workflow_id, first.path_id, first.identity, first.config, first.rank),
            ("", "", PREVIEW_IDENTITY, {"agent_id": ""}, "a0"),
        )
        self.assertEqual((NodePath.objects.count(), Workflow.objects.count()), (0, 0))
        # A sheet node for the same account is a different row: the
        # workflow is part of the key.
        sheet_node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertNotEqual(sheet_node.id, first.id)

    def test_get_node_is_account_scoped(self) -> None:
        node = self.workflows.get_or_create_preview_node()
        self.assertEqual(self.workflows.get_node(str(node.id)).id, node.id)
        with self.assertRaises(NodeNotFound):
            WorkflowService(account_id="01AC" + "Z" * 22).get_node(str(node.id))
        with self.assertRaises(NodeNotFound):
            self.workflows.get_node("01ND" + "0" * 22)

    def test_the_agent_hop_refuses_any_kind_but_column_agent(self) -> None:
        # Refused on the KIND, before the config is parsed: a node of
        # another kind has no agent to find, however its blob is shaped.
        with self.assertRaisesMessage(WrongNodeKind, "expected 'column_agent'"):
            agent_id_of(Node(kind="webhook", config={"agent_id": "01AG" + "A" * 22}))


class AdmissionNodeTests(AdmissionTestCase):
    def test_one_admit_binds_every_output_column_to_one_node(self) -> None:
        config = quick_config(
            outputs=[
                AgentOutput(key="email", label="Email", type="email"),
                AgentOutput(key="status", label="Status", type="text"),
            ]
        )
        fill = self.admit(config=config)
        self.sheet.refresh_from_db()
        node = Node.objects.get(kind=ColumnAgent.KIND)
        self.assertEqual(columns_by_node(self.sheet), {str(node.id): ["email", "status"]})
        self.assertEqual(columns_for_node(self.sheet, str(node.id)), ("email", "status"))
        self.assertEqual(columns_for_node(self.sheet, "01ND" + "0" * 22), ())
        self.assertEqual({t.node_id for t in NodeRun.objects.filter(fill_run_id=str(fill.id))}, {str(node.id)})
        self.assertEqual(agent_id_of(node), consent_of(str(fill.id)).agent_id)

    def test_every_workflow_column_maps_to_its_node_whatever_the_kind(self) -> None:
        self.admit()
        destinations = WebhookDestinationService(account_id=ACCOUNT, user_id=USER)
        destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})
        webhook_columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        sheet = webhook_columns.add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=3600,
        )
        agent = Node.objects.get(kind=ColumnAgent.KIND)
        webhook = Node.objects.get(kind=Webhook.KIND)
        self.assertEqual(columns_by_node(sheet), {str(agent.id): ["answer"], str(webhook.id): ["crm_sync"]})

    def test_a_refill_reuses_the_column_s_node(self) -> None:
        fill = self.admit()
        settle_all(str(fill.id))
        self.fills.cancel(str(fill.id))
        self.lists.add_rows(self.sheet, [{"company": "third.io"}])
        again = self.admission.refill(list_id=str(self.sheet.id), column_key="answer")
        tick_jobs()
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (2, 1))
        self.assertEqual(
            {t.node_id for t in NodeRun.objects.filter(fill_run_id=str(again.id))},
            {str(Node.objects.get(kind=ColumnAgent.KIND).id)},
        )

    def test_different_agents_on_one_sheet_share_the_workflow_on_separate_paths(self) -> None:
        self.admit()
        self.admit(config=quick_config(outputs=[AgentOutput(key="other", label="Other", type="text")]))
        workflow = Workflow.objects.get()
        nodes = list(Node.objects.filter(kind=ColumnAgent.KIND))
        self.assertEqual(len(nodes), 2)
        self.assertEqual({n.workflow_id for n in nodes}, {str(workflow.id)})
        self.assertEqual(len({n.path_id for n in nodes}), 2)
        self.assertEqual(NodePath.objects.filter(workflow_id=str(workflow.id)).count(), 2)

    def test_the_same_agent_on_two_sheets_gets_a_node_per_workflow(self) -> None:
        agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())
        other = self.lists.create(
            owner_id=USER,
            label="Other",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(other, [{"company": "b.com"}])
        self.admit(config=None, agent_id=str(agent.id))
        self.admit(config=None, agent_id=str(agent.id), list_id=str(other.id), confirmed_row_count=1)
        self.assertEqual(Workflow.objects.count(), 2)
        self.assertEqual(Node.objects.filter(identity=str(agent.id)).count(), 2)

    def test_a_refused_admission_leaves_no_node(self) -> None:
        # The node is minted before the eligible walk, so a refusal
        # after it must roll the node back with the fill.
        with self.assertRaises(NoEligibleRows):
            self.admit(config=quick_config(prompt="Find the answer for {{missing}}"))
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (0, 0, 0))


class ListDeleteTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())

    def test_a_list_delete_takes_all_of_its_children_or_none(self) -> None:
        self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        with (
            patch("lists.services.workflows.NodePath.objects.filter", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            self.workflows.delete_for_list(str(self.sheet.id))
        # The nodes were deleted first and must come back with the failure.
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (2, 1, 1))

    def test_deleting_the_list_removes_its_workflow_and_leaves_the_preview_node(self) -> None:
        self.admit()
        preview = WorkflowService(account_id=ACCOUNT).get_or_create_preview_node()
        self.lists.delete(self.sheet)
        self.assertEqual((Workflow.objects.count(), NodePath.objects.count()), (0, 0))
        self.assertEqual(list(Node.objects.values_list("id", flat=True)), [preview.id])


class HeadMarkerInvariantTests(AdmissionTestCase):
    """Every path's head is exactly one MARKER, through every gesture
    that writes a path: entry when nothing feeds the path, wait_until
    when the paths it names do. A path headed by neither is reachable
    by no reaction; one headed by two would be judged twice."""

    def setUp(self) -> None:
        super().setUp()
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        destinations = WebhookDestinationService(account_id=ACCOUNT, user_id=USER)
        self.destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})

    def _assert_invariant(self, where: str) -> None:
        paths = NodePath.objects.filter(account_id=ACCOUNT)
        for path in paths:
            nodes = self.workflows.nodes_on_path(str(path.id))
            with self.subTest(where=where, path=str(path.id)):
                self.assertTrue(nodes, "a path with no node heads with nothing")
                self.assertIn(nodes[0].kind, HEAD_OF_PATH_MARKERS)
                self.assertEqual([n.kind in HEAD_OF_PATH_MARKERS for n in nodes].count(True), 1)
        # And no node of this account points at a path that is gone (the
        # preview node points at none at all).
        live = {str(path.id) for path in paths}
        stranded = [
            str(node.id)
            for node in Node.objects.filter(account_id=ACCOUNT)
            if node.path_id and node.path_id not in live
        ]
        self.assertEqual(stranded, [], where)

    def test_every_gesture_leaves_each_path_headed_by_one_marker(self) -> None:
        self.admit()
        self._assert_invariant("an agent column")
        self.sheet.refresh_from_db()
        self.sheet = self.columns.add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=3600,
        )
        self._assert_invariant("a webhook column")
        self.columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=900,
            enabled=True,
        )
        self._assert_invariant("a webhook column rewritten in place")
        ColumnService(account_id=ACCOUNT, user_id=USER).delete(str(self.sheet.id), key="crm_sync")
        self._assert_invariant("the webhook column's delete")
        self.lists.delete(self.sheet)
        self.assertEqual((NodePath.objects.count(), Node.objects.count()), (0, 0))


class PathTests(AdmissionTestCase):
    """The kind-blind path writers: configs built in memory, persisted
    in rank order on the path they land on."""

    def setUp(self) -> None:
        super().setUp()
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())
        self.agent_node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))

    def _configs(self, destination_id: str = "01DST" + "A" * 21) -> list[NodeConfig]:
        wait = WaitUntil(inbound_path_ids=[self.agent_node.path_id])
        webhook = Webhook(destination_id=destination_id, payload_keys=["company"], interval_seconds=3600)
        return [wait, webhook]

    def test_create_path_stores_the_configs_in_rank_order_on_the_new_path(self) -> None:
        path, nodes = self.workflows.create_path(self.sheet, self._configs())
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (4, 2, 1))
        # The path kinds carry no identity: the slot is the rank.
        self.assertEqual(
            [(n.kind, n.rank, n.path_id, n.identity) for n in nodes],
            [
                (WaitUntil.KIND, "a0", str(path.id), ""),
                (Webhook.KIND, "a1", str(path.id), ""),
            ],
        )
        self.assertEqual(config_as(nodes[0], WaitUntil).inbound_path_ids, [self.agent_node.path_id])
        self.assertEqual(config_as(nodes[1], Webhook).interval_seconds, 3600)
        self.assertEqual(self.workflows.nodes_on_path(str(path.id)), nodes)
        # The agent node's path is untouched and still holds its marker
        # and its node.
        self.assertEqual(
            [(n.kind, n.rank) for n in self.workflows.nodes_on_path(self.agent_node.path_id)],
            [(Entry.KIND, "a0"), (ColumnAgent.KIND, "a1")],
        )

    def test_a_path_may_hold_several_webhook_nodes_and_a_workflow_several_paths(self) -> None:
        # FAILS if a blank identity were still under the identity key:
        # the second webhook node, and the second path's wait node,
        # would collide on (account, workflow, kind, "").
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, nodes = self.workflows.create_path(self.sheet, [wait, webhook, second])
        self.assertEqual(
            [(n.kind, n.rank) for n in nodes], [(WaitUntil.KIND, "a0"), (Webhook.KIND, "a1"), (Webhook.KIND, "a2")]
        )
        other, _ = self.workflows.create_path(self.sheet, self._configs())
        self.assertNotEqual(other.id, path.id)
        self.assertEqual(Node.objects.filter(kind=WaitUntil.KIND).count(), 2)

    def test_a_move_writes_one_node_and_a_no_op_writes_nothing(self) -> None:
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (first, middle, last) = self.workflows.create_path(self.sheet, [wait, webhook, second])
        before = {str(n.id): n.rank for n in self.workflows.nodes_on_path(str(path.id))}
        moved = self.workflows.move_node(str(path.id), str(last.id), after_id=str(first.id))
        after = {str(n.id): n.rank for n in self.workflows.nodes_on_path(str(path.id))}
        self.assertEqual([n.id for n in self.workflows.nodes_on_path(str(path.id))], [first.id, last.id, middle.id])
        self.assertEqual({node_id for node_id in before if before[node_id] != after[node_id]}, {str(moved.id)})
        # Dropped on itself, or right after the node it already follows:
        # no write at all, not merely the same key (a rewrite of the same
        # key would still bump updated_at).
        stamps = {str(n.id): n.updated_at for n in self.workflows.nodes_on_path(str(path.id))}
        self.workflows.move_node(str(path.id), str(last.id), after_id=str(last.id))
        self.workflows.move_node(str(path.id), str(last.id), after_id=str(first.id))
        self.assertEqual({str(n.id): n.rank for n in self.workflows.nodes_on_path(str(path.id))}, after)
        self.assertEqual({str(n.id): n.updated_at for n in self.workflows.nodes_on_path(str(path.id))}, stamps)
        with self.assertRaises(NodeNotFound):
            self.workflows.move_node(str(path.id), str(first.id), after_id="01ND" + "0" * 22)
        with self.assertRaises(PathNotFound):
            WorkflowService(account_id="01AC" + "Z" * 22).move_node(str(path.id), str(first.id), after_id=str(last.id))
        with self.assertRaises(PathNotFound):
            self.workflows.move_node("01NP" + "0" * 22, str(first.id), after_id=str(last.id))

    def test_moves_into_one_gap_re_space_the_path_in_place(self) -> None:
        # Two nodes leapfrogging into the same gap deepen the key by about
        # a character per six moves; past the rebalance length the path
        # is re-spaced synchronously, in the new order, and logged.
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (first, middle, last) = self.workflows.create_path(self.sheet, [wait, webhook, second])
        with (
            patch("lists.services.workflows.RANK_REBALANCE_LENGTH", 3),
            self.assertLogs("lists.services.workflows", "WARNING"),
        ):
            for n in range(12):
                mover = middle if n % 2 == 0 else last
                self.workflows.move_node(str(path.id), str(mover.id), after_id=str(first.id))
        nodes = self.workflows.nodes_on_path(str(path.id))
        self.assertEqual([n.id for n in nodes], [first.id, last.id, middle.id])
        self.assertLessEqual(max(len(n.rank) for n in nodes), 3)

    def test_the_re_space_keeps_the_order_the_drop_asked_for(self) -> None:
        # One move past the bound, so the re-space is the LAST write and
        # the order read back is the order it wrote. FAILS if the moved
        # node lands beside the wrong neighbour, or if a fresh key is
        # one the path already held.
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (first, middle, last) = self.workflows.create_path(self.sheet, [wait, webhook, second])
        with (
            patch("lists.services.workflows.RANK_REBALANCE_LENGTH", 2),
            self.assertLogs("lists.services.workflows", "WARNING"),
        ):
            self.workflows.move_node(str(path.id), str(last.id), after_id=str(first.id))
        nodes = self.workflows.nodes_on_path(str(path.id))
        self.assertEqual([n.id for n in nodes], [first.id, last.id, middle.id])
        self.assertEqual([n.rank for n in nodes], ["Zx", "Zy", "Zz"])

    def test_a_paths_barrier_stays_first(self) -> None:
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (barrier, first_hook, second_hook) = self.workflows.create_path(self.sheet, [wait, webhook, second])
        with self.assertRaises(PathHeadFixed):
            self.workflows.move_node(str(path.id), str(barrier.id), after_id=str(first_hook.id))
        # Behind the barrier the nodes move freely.
        self.workflows.move_node(str(path.id), str(second_hook.id), after_id=str(barrier.id))
        nodes = self.workflows.nodes_on_path(str(path.id))
        self.assertEqual([n.id for n in nodes], [barrier.id, second_hook.id, first_hook.id])
        # The OTHER marker heads the agent path, and is as fixed: the
        # guard is the marker set, not one kind.
        entry, agent = self.workflows.nodes_on_path(self.agent_node.path_id)
        with self.assertRaises(PathHeadFixed):
            self.workflows.move_node(self.agent_node.path_id, str(entry.id), after_id=str(agent.id))

    def test_every_writer_of_a_paths_nodes_takes_the_paths_lock_first(self) -> None:
        # One lock, one order: the path row FOR UPDATE before any node
        # write, so a move, a config replace, and a delete never
        # interleave and never wait on each other the other way round.
        # A self-drop reads nothing, so it takes no lock at all.
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (barrier, _first_hook, second_hook) = self.workflows.create_path(self.sheet, [wait, webhook, second])

        def path_lock_precedes_node_writes(queries) -> bool:
            sql = [q["sql"] for q in queries]
            locks = [i for i, q in enumerate(sql) if "FOR UPDATE" in q and "lists_nodepath" in q]
            writes = [i for i, q in enumerate(sql) if q.startswith(("UPDATE", "DELETE")) and "lists_node" in q]
            return bool(locks) and (not writes or locks[0] < writes[0])

        with CaptureQueriesContext(connection) as moved:
            self.workflows.move_node(str(path.id), str(second_hook.id), after_id=str(barrier.id))
        self.assertTrue(path_lock_precedes_node_writes(moved.captured_queries))
        with CaptureQueriesContext(connection) as self_drop:
            self.workflows.move_node(str(path.id), str(second_hook.id), after_id=str(second_hook.id))
        self.assertFalse(any("FOR UPDATE" in q["sql"] for q in self_drop.captured_queries))
        with CaptureQueriesContext(connection) as replaced:
            self.workflows.replace_path_nodes(str(path.id), [wait, second, webhook])
        self.assertTrue(path_lock_precedes_node_writes(replaced.captured_queries))
        with CaptureQueriesContext(connection) as deleted:
            self.workflows.delete_path(str(path.id))
        self.assertTrue(path_lock_precedes_node_writes(deleted.captured_queries))
        with CaptureQueriesContext(connection) as swept:
            self.workflows.delete_for_list(str(self.sheet.id))
        self.assertTrue(path_lock_precedes_node_writes(swept.captured_queries))

    def test_a_node_with_no_rank_is_refused_at_the_insert(self) -> None:
        # A path-shaped node (the one whose order matters): a CharField
        # silently stores "" when a writer forgets the rank, and the
        # check constraint makes that a failed insert instead of a node
        # sorted first on its path forever.
        path, _ = self.workflows.create_path(self.sheet, self._configs())
        with self.assertRaisesMessage(IntegrityError, "node_rank_named"):
            Node.objects.create(
                account_id=ACCOUNT, workflow_id=self.agent_node.workflow_id, path_id=str(path.id), kind="webhook"
            )

    def test_a_path_holds_exactly_one_head_marker_and_it_heads_the_path(self) -> None:
        # A path says what starts it: entry (fed by nothing) or a wait
        # (fed by the paths it names). One that says nothing is
        # reachable by neither reaction. One that says it TWICE is worse
        # than refused work: the marker behind is read as a head by
        # everything that looks for one, so an arrival would start the
        # node behind IT, barrier and all. Both shapes are refused and
        # write nothing. FAILS if either half of the guard goes.
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        before = (Node.objects.count(), NodePath.objects.count())
        refused: list[list[NodeConfig]] = [
            [webhook, second],
            [],
            [wait, Entry(), webhook],
            [Entry(), wait, webhook],
            [Entry(), webhook, Entry()],
        ]
        for nodes in refused:
            with self.subTest(kinds=[config.KIND for config in nodes]), self.assertRaises(WrongNodeKind):
                self.workflows.create_path(self.sheet, nodes)
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), before)

    def test_a_path_headed_by_an_entry_is_written(self) -> None:
        # The accept half, and the OTHER member of the marker set: the
        # refusals above prove a shape is turned away, not that a
        # legitimate one still lands. FAILS if the guard narrows to one
        # marker kind.
        _wait, webhook = self._configs()
        path, nodes = self.workflows.create_path(self.sheet, [Entry(), webhook])
        self.assertEqual([n.kind for n in nodes], [Entry.KIND, Webhook.KIND])
        self.assertEqual([n.path_id for n in nodes], [str(path.id), str(path.id)])
        self.assertLess(nodes[0].rank, nodes[1].rank)

    def test_a_move_never_crosses_paths(self) -> None:
        path, (barrier, hook) = self.workflows.create_path(self.sheet, self._configs())
        other, (_other_barrier, other_hook) = self.workflows.create_path(self.sheet, self._configs())
        with self.assertRaises(NodeNotFound):  # a node from another path is not this path's to move
            self.workflows.move_node(str(path.id), str(other_hook.id), after_id=str(barrier.id))
        with self.assertRaises(NodeNotFound):  # nor its to anchor to
            self.workflows.move_node(str(path.id), str(hook.id), after_id=str(other_hook.id))
        self.assertEqual(
            [n.id for n in self.workflows.nodes_on_path(str(other.id))], [_other_barrier.id, other_hook.id]
        )

    def test_a_config_replace_lands_by_rank_order_after_a_move(self) -> None:
        # replace_path_nodes zips the path's RANK order against the
        # configs it is handed: after a move, the second config lands on
        # the node that is now second. The caller owns that contract.
        wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (barrier, first_hook, second_hook) = self.workflows.create_path(self.sheet, [wait, webhook, second])
        self.workflows.move_node(str(path.id), str(second_hook.id), after_id=str(barrier.id))
        replaced = self.workflows.replace_path_nodes(str(path.id), [wait, webhook, second])
        self.assertEqual([n.id for n in replaced], [barrier.id, second_hook.id, first_hook.id])
        self.assertEqual(config_as(replaced[1], Webhook).destination_id, webhook.destination_id)

    def test_a_failed_node_write_rolls_the_path_back(self) -> None:
        # FAILS if create_path loses its transaction: the path survives.
        with (
            patch("lists.services.workflows.Node.objects.create", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            self.workflows.create_path(self.sheet, self._configs())
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (2, 1))

    def test_config_as_refuses_the_wrong_kind_and_save_node_refuses_a_foreign_config(self) -> None:
        _, nodes = self.workflows.create_path(self.sheet, self._configs())
        with self.assertRaises(WrongNodeKind):
            config_as(nodes[0], Webhook)
        with self.assertRaises(WrongNodeKind):
            self.workflows.save_node(nodes[0], Webhook(destination_id="d", payload_keys=[]))

    def test_replace_path_nodes_rewrites_each_config_in_place_and_keeps_the_shape(self) -> None:
        path, nodes = self.workflows.create_path(self.sheet, self._configs())
        replaced = self.workflows.replace_path_nodes(str(path.id), self._configs(destination_id="01DST" + "B" * 21))
        self.assertEqual([n.id for n in replaced], [n.id for n in nodes])
        self.assertEqual(config_as(replaced[1], Webhook).destination_id, "01DST" + "B" * 21)
        with self.assertRaises(WrongNodeKind):
            self.workflows.replace_path_nodes(str(path.id), self._configs()[:1])

    def test_the_config_queries_read_the_json_and_are_account_scoped(self) -> None:
        path, _ = self.workflows.create_path(self.sheet, self._configs())
        workflow_id = self.agent_node.workflow_id
        over = self.workflows.waits_on(workflow_id, self.agent_node.path_id)
        self.assertEqual([n.path_id for n in over], [str(path.id)])
        self.workflows.create_path(self.sheet, self._configs())
        self.assertEqual(self.workflows.webhook_nodes_for("01DST" + "A" * 21).count(), 2)
        foreign = WorkflowService(account_id="01ACCT" + "Z" * 20)
        self.assertEqual(foreign.webhook_nodes_for("01DST" + "A" * 21).count(), 0)
        self.assertEqual(foreign.waits_on(workflow_id, self.agent_node.path_id).count(), 0)

    def test_the_wait_scan_is_this_workflows_and_this_sheets_alone(self) -> None:
        # A path id is only ever fed from its own sheet, so a barrier on
        # ANOTHER sheet naming this path (corruption, or a future copy)
        # is not this path's to follow. FAILS if the scan drops its
        # workflow filter and goes account-wide again.
        mine, _ = self.workflows.create_path(self.sheet, self._configs())
        other_sheet = self.lists.create(
            owner_id=USER,
            label="Other",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        # The writer is kind-blind about the ids a config carries, so a
        # wait on another sheet can name this sheet's path.
        self.workflows.create_path(
            other_sheet,
            [
                WaitUntil(inbound_path_ids=[self.agent_node.path_id]),
                Webhook(destination_id="01DST" + "C" * 21, payload_keys=["company"]),
            ],
        )
        found = self.workflows.waits_on(self.agent_node.workflow_id, self.agent_node.path_id)
        self.assertEqual([n.path_id for n in found], [str(mine.id)])

    def test_the_entry_iterate_is_lazy_and_one_read_of_this_workflows_markers(self) -> None:
        # The trigger walks a sheet's entries one at a time: a generator
        # that issues nothing until it is pulled, and ONE index range
        # over (account, workflow, kind) however many entries there are.
        # FAILS if it returns a list, or loses its workflow filter.
        import inspect

        second = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id="01AGT" + "Z" * 21)
        other_sheet = self.lists.create(
            owner_id=USER,
            label="Other",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.workflows.get_or_create_column_agent_node(other_sheet, agent_id="01AGT" + "Y" * 21)
        workflow_id = self.agent_node.workflow_id
        with CaptureQueriesContext(connection) as unpulled:
            entries = self.workflows.iter_entry_nodes(workflow_id)
            self.assertTrue(inspect.isgenerator(entries))
        # Nothing is read until the caller pulls: the trigger holds one
        # marker at a time, never the workflow's.
        self.assertEqual([q["sql"] for q in unpulled.captured_queries], [])
        with CaptureQueriesContext(connection) as captured:
            found = list(entries)
        self.assertEqual({n.path_id for n in found}, {self.agent_node.path_id, second.path_id})
        reads = [q["sql"] for q in captured.captured_queries if "lists_node" in q["sql"]]
        self.assertEqual(len(reads), 1, reads)
        self.assertIn("workflow_id", reads[0])
        self.assertIn("'entry'", reads[0])

    def test_delete_path_removes_exactly_its_nodes_and_itself(self) -> None:
        path, _ = self.workflows.create_path(self.sheet, self._configs())
        self.workflows.delete_path(str(path.id))
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (2, 1, 1))
        self.assertEqual(Node.objects.get(kind=ColumnAgent.KIND).id, self.agent_node.id)

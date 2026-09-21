"""The workflow substrate: one workflow per sheet, one path per node,
one node per (agent, sheet), minted by admission on demand and removed
only with the list; the bench node is the account's one sheetless
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

from ..models import Node, NodePath, NodeRun, Workflow
from ..nodes.base import NodeConfig
from ..nodes.column_agent import BENCH_IDENTITY
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from ..services.fill_admission import NoEligibleRows
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
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (1, 1, 1))
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
        # And the next call mints the pair cleanly.
        node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertEqual(node.path_id, str(NodePath.objects.get().id))

    def test_the_bench_node_is_one_per_account_with_no_workflow_or_path(self) -> None:
        first = self.workflows.get_or_create_bench_node()
        second = self.workflows.get_or_create_bench_node()
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            (first.workflow_id, first.path_id, first.identity, first.config, first.rank),
            ("", "", BENCH_IDENTITY, {"agent_id": ""}, "a0"),
        )
        self.assertEqual((NodePath.objects.count(), Workflow.objects.count()), (0, 0))
        # A sheet node for the same account is a different row: the
        # workflow is part of the key.
        sheet_node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.assertNotEqual(sheet_node.id, first.id)

    def test_get_node_is_account_scoped(self) -> None:
        node = self.workflows.get_or_create_bench_node()
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
        node = Node.objects.get()
        self.assertEqual(columns_by_node(self.sheet), {str(node.id): ["email", "status"]})
        self.assertEqual(columns_for_node(self.sheet, str(node.id)), ("email", "status"))
        self.assertEqual(columns_for_node(self.sheet, "01ND" + "0" * 22), ())
        self.assertEqual({t.node_id for t in NodeRun.objects.filter(fill_run_id=str(fill.id))}, {str(node.id)})
        self.assertEqual(agent_id_of(node), consent_of(str(fill.id)).agent_id)

    def test_a_refill_reuses_the_column_s_node(self) -> None:
        fill = self.admit()
        settle_all(str(fill.id))
        self.fills.cancel(str(fill.id))
        self.lists.add_rows(self.sheet, [{"company": "third.io"}])
        again = self.admission.refill(list_id=str(self.sheet.id), column_key="answer")
        tick_jobs()
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (1, 1))
        self.assertEqual(
            {t.node_id for t in NodeRun.objects.filter(fill_run_id=str(again.id))}, {str(Node.objects.get().id)}
        )

    def test_different_agents_on_one_sheet_share_the_workflow_on_separate_paths(self) -> None:
        self.admit()
        self.admit(config=quick_config(outputs=[AgentOutput(key="other", label="Other", type="text")]))
        workflow = Workflow.objects.get()
        nodes = list(Node.objects.all())
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
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (1, 1, 1))

    def test_deleting_the_list_removes_its_workflow_and_leaves_the_bench_node(self) -> None:
        self.admit()
        bench = WorkflowService(account_id=ACCOUNT).get_or_create_bench_node()
        self.lists.delete(self.sheet)
        self.assertEqual((Workflow.objects.count(), NodePath.objects.count()), (0, 0))
        self.assertEqual(list(Node.objects.values_list("id", flat=True)), [bench.id])


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
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (3, 2, 1))
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
        # The agent node's path is untouched and still holds its one node.
        self.assertEqual([n.rank for n in self.workflows.nodes_on_path(self.agent_node.path_id)], ["a0"])

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
        self.workflows.move_node(str(path.id), str(first.id), after_id=None)
        self.assertEqual({str(n.id): n.rank for n in self.workflows.nodes_on_path(str(path.id))}, after)
        self.assertEqual({str(n.id): n.updated_at for n in self.workflows.nodes_on_path(str(path.id))}, stamps)
        with self.assertRaises(NodeNotFound):
            self.workflows.move_node(str(path.id), str(first.id), after_id="01ND" + "0" * 22)
        with self.assertRaises(PathNotFound):
            WorkflowService(account_id="01AC" + "Z" * 22).move_node(str(path.id), str(first.id), after_id=None)
        with self.assertRaises(PathNotFound):
            self.workflows.move_node("01NP" + "0" * 22, str(first.id), after_id=None)

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
            self.workflows.move_node(str(path.id), str(first_hook.id), after_id=None)
        with self.assertRaises(PathHeadFixed):
            self.workflows.move_node(str(path.id), str(barrier.id), after_id=str(first_hook.id))
        # Behind the barrier the nodes move freely.
        self.workflows.move_node(str(path.id), str(second_hook.id), after_id=str(barrier.id))
        nodes = self.workflows.nodes_on_path(str(path.id))
        self.assertEqual([n.id for n in nodes], [barrier.id, second_hook.id, first_hook.id])

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

    def test_a_re_space_from_a_drop_at_the_top_puts_the_node_first(self) -> None:
        # A path with no barrier, so the top is open: the re-space's
        # insertion point at the head is the branch this drives.
        _wait, webhook = self._configs()
        second = Webhook(destination_id="01DST" + "B" * 21, payload_keys=["company"])
        path, (first, last) = self.workflows.create_path(self.sheet, [webhook, second])
        with (
            patch("lists.services.workflows.RANK_REBALANCE_LENGTH", 1),
            self.assertLogs("lists.services.workflows", "WARNING"),
        ):
            self.workflows.move_node(str(path.id), str(last.id), after_id=None)
        self.assertEqual([n.id for n in self.workflows.nodes_on_path(str(path.id))], [last.id, first.id])

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
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (1, 1))

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
        naming = self.workflows.wait_nodes_naming(self.agent_node.path_id)
        self.assertEqual([n.path_id for n in naming], [str(path.id)])
        self.workflows.create_path(self.sheet, self._configs())
        self.assertEqual(self.workflows.webhook_nodes_for("01DST" + "A" * 21).count(), 2)
        foreign = WorkflowService(account_id="01ACCT" + "Z" * 20)
        self.assertEqual(foreign.webhook_nodes_for("01DST" + "A" * 21).count(), 0)
        self.assertEqual(foreign.wait_nodes_naming(self.agent_node.path_id).count(), 0)

    def test_path_of_column_walks_the_ai_column_to_its_node(self) -> None:
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(self.agent_node.id)},
        ]
        self.assertEqual(self.workflows.path_of_column(self.sheet, "answer"), self.agent_node.path_id)
        with self.assertRaises(NodeNotFound):
            self.workflows.path_of_column(self.sheet, "company")

    def test_delete_path_removes_exactly_its_nodes_and_itself(self) -> None:
        path, _ = self.workflows.create_path(self.sheet, self._configs())
        self.workflows.delete_path(str(path.id))
        self.assertEqual((Node.objects.count(), NodePath.objects.count(), Workflow.objects.count()), (1, 1, 1))
        self.assertEqual(Node.objects.get().id, self.agent_node.id)

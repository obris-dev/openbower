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

from openbower_schema.agents import AgentOutput

from ..models import Node, NodePath, NodeRun, Workflow
from ..services.fill_admission import NoEligibleRows
from ..services.workflows import (
    NodeNotFound,
    WorkflowService,
    WrongNodeKind,
    agent_id_of,
    columns_by_node,
    columns_for_node,
)
from .fill_helpers import settle_all
from .test_fill_admission import ACCOUNT, USER, AdmissionTestCase, quick_config


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
            (first.workflow_id, first.path_id, first.identity, first.config), ("", "", "", {"agent_id": ""})
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
        self.assertEqual(agent_id_of(node), fill.agent_id)

    def test_a_refill_reuses_the_column_s_node(self) -> None:
        fill = self.admit()
        settle_all(str(fill.id))
        self.fills.cancel(str(fill.id))
        self.lists.add_rows(self.sheet, [{"company": "third.io"}])
        again = self.admission.refill(list_id=str(self.sheet.id), column_key="answer")
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
            columns=[{"key": "company", "label": "Company", "type": "text"}],
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

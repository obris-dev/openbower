"""A person setting cells on a row: the values replace what is there,
the type refuses what does not fit, a hand-filled AI cell is recorded
as the person's and completes the barriers waiting on it.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_set_cells
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from agents.services import AgentService
from openbower_kernel.fields import new_ulid
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.lists import AiColumn
from webhooks.services import WebhookDestinationService

from ..constants import CellSource, StoredCellState
from ..models import ListCellState, NodeRun
from ..nodes.column_agent import ColumnAgent
from ..nodes.entry import Entry
from ..operations.set_cells import SetCellsOperation
from ..services import cell_truth
from ..services.ai_columns import AiColumnService
from ..services.lists import CellBlank, CellColumnUnknown, CellNotWritable, CellTypeRefused, ListService, RowNotFound
from ..services.webhook_columns import WebhookColumnService
from ..services.workflow_reactions import WorkflowReactions
from ..services.workflows import WorkflowService
from .fill_helpers import chain_behind

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"

_CONFIG = AgentConfig(
    prompt="Find the answer for {{company}}",
    provider="openai_compatible",
    source="ollama",
    model="test-model",
    tools=AgentTools(),
    outputs=[AgentOutput(key="answer", label="Answer", type="text")],
)


class SetCellsTests(TestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[
                {"kind": "plain", "key": "company", "label": "Company", "type": "text"},
                {"kind": "plain", "key": "employees", "label": "Employees", "type": "number"},
            ],
            origin="manual",
        )
        self.row, self.other = self.lists.add_rows(
            self.sheet, [{"company": "acme.com", "employees": "10"}, {"company": "example.io"}]
        )
        patcher = patch("lists.services.runnable.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_cells(self, cells: dict[str, str], row_id: str = ""):
        self.sheet.refresh_from_db()
        return SetCellsOperation(
            account_id=ACCOUNT, target_list=self.sheet, row_id=row_id or str(self.row.id), cells=cells
        ).run()

    def add_ai_column(self) -> str:
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(self.sheet.id), config=_CONFIG, agent_id="")
        self.sheet.refresh_from_db()
        return next(column.node_id for column in self.sheet.columns if column.key == "answer")

    def test_a_value_replaces_what_the_cell_held(self):
        report = self.set_cells({"company": "Acme Holdings", "employees": "12"})
        self.assertEqual(report.row.data, {"company": "Acme Holdings", "employees": "12"})
        self.assertEqual(report.queued, 0)
        self.other.refresh_from_db()
        self.assertEqual(self.other.data, {"company": "example.io"})

    def test_a_value_outside_the_type_is_refused_and_nothing_is_written(self):
        with self.assertRaises(CellTypeRefused) as refused:
            self.set_cells({"company": "Acme Holdings", "employees": "lots"})
        self.assertIn("Employees", str(refused.exception))
        self.row.refresh_from_db()
        self.assertEqual(self.row.data, {"company": "acme.com", "employees": "10"})

    def test_a_blank_an_unknown_column_and_a_gone_row_are_refused(self):
        with self.assertRaises(CellBlank):
            self.set_cells({"company": "   "})
        with self.assertRaises(CellColumnUnknown):
            self.set_cells({"revenue": "1"})
        with self.assertRaises(RowNotFound):
            self.set_cells({"company": "Acme"}, row_id=new_ulid())

    def test_a_webhook_column_takes_no_value(self):
        self.add_ai_column()
        destinations = WebhookDestinationService(account_id=ACCOUNT, user_id=USER)
        destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})
        WebhookColumnService(account_id=ACCOUNT, user_id=USER).add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=3600,
        )
        with self.assertRaises(CellNotWritable):
            self.set_cells({"crm_sync": "sent"})

    def test_a_hand_filled_ai_cell_is_recorded_as_the_persons(self):
        # Over a run's own record: the paste re-attributes the cell to
        # the person and drops the run it was credited to, so the
        # drawer names the right author. FAILS if the ledger's upsert
        # keeps either from the record it found.
        self.add_ai_column()
        cell_truth.write(
            account_id=ACCOUNT,
            list_id=str(self.sheet.id),
            row_id=str(self.row.id),
            fill_run_id=new_ulid(),
            states={"answer": StoredCellState.FILLED},
            tools={},
            source=CellSource.NODE,
        )
        report = self.set_cells({"answer": "42"})
        self.assertEqual(report.row.data["answer"], "42")
        record = ListCellState.objects.get(row_id=str(self.row.id), column_key="answer")
        self.assertEqual(record.state, StoredCellState.FILLED)
        self.assertEqual(record.source, CellSource.MANUAL)
        self.assertIsNone(record.fill_run_id)

    def test_pasting_the_value_already_there_leaves_the_record_alone(self):
        # The record's stamp is when the row completed as far as a
        # barrier is concerned, so an unchanged paste must not move it,
        # nor tell the workflow anything. FAILS if the landing counts
        # an unchanged overwrite as written.
        self.add_ai_column()
        self.set_cells({"answer": "by hand"})
        record = ListCellState.objects.get(row_id=str(self.row.id), column_key="answer")
        report = self.set_cells({"answer": "by hand"})
        self.assertEqual(report.queued, 0)
        again = ListCellState.objects.get(row_id=str(self.row.id), column_key="answer")
        self.assertEqual(again.updated_at, record.updated_at)
        self.assertEqual(report.row.data["answer"], "by hand")

    def test_a_hand_filled_ai_cell_completes_the_barrier_waiting_on_it(self):
        # The barrier half of what a run's answer owes the workflow,
        # owed by the hand too: the node behind the barrier gets its run
        # for this row and no other. FAILS if the operation lands
        # without completing.
        upstream = self.add_ai_column()
        chained = chain_behind(self.sheet, upstream_node_id=upstream, key="later")
        report = self.set_cells({"answer": "42"})
        self.assertEqual(report.queued, 1)
        runs = NodeRun.objects.filter(node_id=str(chained.id))
        self.assertEqual([run.row_id for run in runs], [str(self.row.id)])

    def test_a_hand_filled_cell_never_runs_the_node_behind_it(self):
        # One cell of a row that may still be being edited: the next
        # node on the path waits for the person to ask. FAILS if the
        # operation advances (rule 1) instead of completing (rule 2).
        def agent(label: str, key: str):
            config = AgentConfig(
                prompt="Find the answer for {{company}}",
                provider="openai_compatible",
                source="ollama",
                model="test-model",
                tools=AgentTools(),
                outputs=[AgentOutput(key=key, label=label, type="text")],
            )
            return AgentService(account_id=ACCOUNT).create(owner_id=USER, label=label, config=config)

        first, second = agent("First", "first"), agent("Second", "second")
        _, nodes = WorkflowService(account_id=ACCOUNT).create_path(
            self.sheet, [Entry(), ColumnAgent(agent_id=str(first.id)), ColumnAgent(agent_id=str(second.id))]
        )
        self.sheet.columns = [
            *self.sheet.columns,
            AiColumn(key="first", label="First", type="text", node_id=str(nodes[1].id)),
            AiColumn(key="second", label="Second", type="text", node_id=str(nodes[2].id)),
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        report = self.set_cells({"first": "by hand"})
        self.assertEqual(report.row.data["first"], "by hand")
        self.assertEqual(report.queued, 0)
        self.assertEqual(NodeRun.objects.filter(node_id=str(nodes[2].id)).count(), 0)

    def test_a_node_still_owes_a_row_whose_other_outputs_a_hand_did_not_fill(self):
        # An agent's columns are one unit, recorded together by its run,
        # so a record in only some of them is a hand's work and the node
        # owes the row the rest. FAILS if "attempted" reads any record
        # as the whole unit (the half-filled row would be skipped for
        # good, and no gesture could ask for the rest).
        two_outputs = _CONFIG.model_copy(
            update={
                "outputs": [
                    AgentOutput(key="answer", label="Answer", type="text"),
                    AgentOutput(key="detail", label="Detail", type="text"),
                ]
            }
        )
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(self.sheet.id), config=two_outputs, agent_id="")
        self.sheet.refresh_from_db()
        node_id = next(column.node_id for column in self.sheet.columns if column.key == "answer")
        self.set_cells({"answer": "by hand"})
        self.set_cells({"answer": "both", "detail": "by hand"}, row_id=str(self.other.id))
        reactions = WorkflowReactions(account_id=ACCOUNT)
        self.assertEqual(reactions.trigger(self.sheet, [self.row]), 1)
        self.assertEqual(reactions.trigger(self.sheet, [self.other]), 0)
        runs = NodeRun.objects.filter(node_id=node_id)
        self.assertEqual([run.row_id for run in runs], [str(self.row.id)])

"""The node-run state machine's processing half: handle_node_run claims
a task (READY | QUEUED -> PROCESSING), resolves the agent live, runs it
through the mocked model seam, and lands the cell with NO fill run. A
duplicate delivery loses the claim CAS and is dropped; a gone row or list
settles terminally; a retriable blank parks; a run at the attempt cap
gives up with a diagnosed blank. The model is a scripted FunctionModel
through the same patched seam the fill worker tests use; everything else
(lists, the state machine, landing) is real.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_consume_node_runs
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import TestCase, override_settings

from agents.models import Agent
from openbower_schema.fills import CellRunResult

from ..constants import NODE_RUN_ATTEMPTS, NodeRunStatus, StoredCellState
from ..models import List, ListCellState, ListRow, Node, NodeRun
from ..operations.consume_node_runs import NodeRunConsumer, handle_node_run
from ..services.fill_processing import ProcessNodeRun
from ..services.workflows import agent_id_of
from .test_fill_worker import _patches, answering_model, throttling_model
from .test_node_runs import AutofillHarness

WORKER = "test:consumer"


class ProcessNodeRunTests(AutofillHarness):
    def _one_ready_task(self, company: str = "target.co"):
        """Push one row to a fresh AI sheet and return its READY null-run
        task plus the row id."""
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": company}])
        [row_id] = self._new_row_ids(sheet, before)
        task = self._null_run_tasks().get(row_id=row_id)
        return sheet, task, row_id

    def _handle(self, task, model) -> str:
        with _patches(model):
            return handle_node_run(str(task.id), WORKER)

    def test_happy_path_claims_writes_the_cell_and_settles_done(self) -> None:
        _, task, row_id = self._one_ready_task()
        self.assertEqual(task.status, NodeRunStatus.READY)

        result = self._handle(task, answering_model(lambda prompt: "found it"))
        self.assertEqual(result, "done")

        # 1) The value lands on the sheet row.
        row = ListRow.objects.get(id=row_id)
        self.assertEqual(row.data["answer"], "found it")
        # 2) A ListCellState, FILLED, with NO fill run (the automatic path).
        cell = ListCellState.objects.get(row_id=row_id, column_key="answer")
        self.assertEqual(cell.state, StoredCellState.FILLED)
        self.assertIsNone(cell.fill_run_id)
        # 3) The task walked to PROCESSING (attempt stamped) and settled DONE.
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.DONE)
        self.assertEqual(task.attempts, 1)
        self.assertEqual(task.leased_by, WORKER)

    def test_a_duplicate_delivery_is_dropped(self) -> None:
        # The claim CAS is the dedup: once the first delivery settled the
        # task terminal, a redelivery finds nothing READY | QUEUED to
        # claim and drops.
        _, task, _ = self._one_ready_task()
        self.assertEqual(self._handle(task, answering_model(lambda prompt: "found it")), "done")

        second = self._handle(task, answering_model(lambda prompt: "should never run"))
        self.assertEqual(second, "dropped")

    def test_a_vanished_row_settles_row_missing(self) -> None:
        _, task, row_id = self._one_ready_task("gone.co")
        ListRow.objects.filter(id=row_id).delete()

        self.assertEqual(handle_node_run(str(task.id), WORKER), "row_missing")

        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.ROW_MISSING)
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_vanished_list_settles_list_missing(self) -> None:
        sheet, task, row_id = self._one_ready_task("orphan.co")
        # Delete ONLY the List row (no cascade): the ListRow survives, so
        # the processor resolves the row but not its list.
        List.objects.filter(id=sheet.id).delete()

        self.assertEqual(handle_node_run(str(task.id), WORKER), "list_missing")

        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.LIST_MISSING)
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_node_with_no_columns_left_settles_done_unrun(self) -> None:
        sheet, task, row_id = self._one_ready_task("moved.co")
        # The column was removed after the push: the node is inert (still
        # a row, nothing binds to it), so there is nothing to run.
        sheet.columns = [column for column in sheet.columns if not column.get("fill")]
        sheet.save(update_fields=["columns", "updated_at"])

        # A scripted model, not the unpatched one: under the test profile
        # an unpatched run raises ModelUnavailable and settles DONE with an
        # empty result, byte-identical to the skip. If the skip ever stops
        # firing, this run REACHES the model and lands a non-empty result.
        self.assertEqual(self._handle(task, answering_model(lambda prompt: "must not run")), "done")

        task.refresh_from_db()
        self.assertEqual((task.status, task.result), (NodeRunStatus.DONE, {}))
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_vanished_node_settles_done_unrun(self) -> None:
        _, task, row_id = self._one_ready_task("nodeless.co")
        # The column still points at the node, but the node row is gone:
        # the agent is unreachable, so the task settles rather than lingers.
        Node.objects.filter(id=task.node_id).delete()

        # Same discriminator as above: a run that got past the node hop
        # would reach this model and land a result. A gone node is
        # corruption, so unlike a gone agent it must leave a trace.
        with self.assertLogs("lists.services.fill_processing.processor", level="WARNING") as logs:
            self.assertEqual(self._handle(task, answering_model(lambda prompt: "must not run")), "done")
        self.assertIn(f"node {task.node_id} is gone", logs.output[0])

        task.refresh_from_db()
        self.assertEqual((task.status, task.result), (NodeRunStatus.DONE, {}))
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_vanished_agent_settles_done_unrun_and_silently(self) -> None:
        _, task, row_id = self._one_ready_task("agentless.co")
        # An agent delete leaves its columns orphaned on purpose, so this
        # settle is the allowed shape, not corruption: no warning.
        Agent.objects.filter(id=agent_id_of(Node.objects.get(id=task.node_id))).delete()

        with self.assertNoLogs("lists.services.fill_processing.processor", level="WARNING"):
            self.assertEqual(self._handle(task, answering_model(lambda prompt: "must not run")), "done")

        task.refresh_from_db()
        self.assertEqual((task.status, task.result), (NodeRunStatus.DONE, {}))
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_crash_inside_the_run_parks_the_task_and_never_escapes(self) -> None:
        _, task, row_id = self._one_ready_task("crash.co")
        with (
            patch("lists.operations.consume_node_runs.AutofillRun.process", side_effect=RuntimeError("boom")),
            self.assertLogs("lists.operations.consume_node_runs", level="ERROR") as logs,
        ):
            self.assertEqual(handle_node_run(str(task.id), WORKER), "parked")
        self.assertIn("crashed on attempt 1", logs.output[0])

        task.refresh_from_db()
        self.assertEqual((task.status, task.attempts), (NodeRunStatus.READY, 1))
        self.assertIsNotNone(task.not_before)
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())

    def test_a_crash_past_the_attempt_cap_settles_done_unrun(self) -> None:
        _, task, _ = self._one_ready_task("crash.co")
        NodeRun.objects.filter(id=task.id).update(attempts=NODE_RUN_ATTEMPTS)
        with (
            patch("lists.operations.consume_node_runs.AutofillRun.process", side_effect=RuntimeError("boom")),
            self.assertLogs("lists.operations.consume_node_runs", level="ERROR"),
        ):
            self.assertEqual(handle_node_run(str(task.id), WORKER), "done")

        task.refresh_from_db()
        self.assertEqual((task.status, task.result), (NodeRunStatus.DONE, {}))

    def test_a_transient_blank_parks_back_to_ready_with_a_backoff(self) -> None:
        _, task, row_id = self._one_ready_task("throttled.co")

        result = self._handle(task, throttling_model())
        self.assertEqual(result, "parked")

        task.refresh_from_db()
        # Parked: back to READY, a real backoff in not_before, nothing
        # diagnosed on the sheet (the cell still shimmers).
        self.assertEqual(task.status, NodeRunStatus.READY)
        self.assertIsNotNone(task.not_before)
        self.assertTrue(task.parked)
        self.assertEqual(task.leased_by, "")
        self.assertFalse(ListCellState.objects.filter(row_id=row_id).exists())
        # The run IS stored (its refusals are the audit the give-up reads).
        self.assertEqual(task.result["declined_cause"], StoredCellState.TRANSIENT)

    def test_at_the_attempt_cap_it_gives_up_with_a_diagnosed_blank(self) -> None:
        _, task, row_id = self._one_ready_task("exhausted.co")
        # One shy of the cap: the claim stamps the attempt that tips it
        # over (attempts > NODE_RUN_ATTEMPTS), so this claim gives up
        # rather than running the model.
        NodeRun.objects.filter(id=task.id).update(attempts=NODE_RUN_ATTEMPTS)

        # The model would answer if reached; give-up must short-circuit it.
        result = self._handle(task, answering_model(lambda prompt: "never reached"))
        self.assertEqual(result, "done")

        row = ListRow.objects.get(id=row_id)
        self.assertNotIn("answer", row.data)  # blank landed, not the model's answer
        cell = ListCellState.objects.get(row_id=row_id, column_key="answer")
        self.assertEqual(cell.state, StoredCellState.NO_EVIDENCE)
        self.assertIsNone(cell.fill_run_id)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.DONE)
        self.assertEqual(task.attempts, NODE_RUN_ATTEMPTS + 1)

    def test_the_give_up_blank_carries_the_blamed_tool(self) -> None:
        # Regression: the autofill give-up dropped blamed_tool while the
        # fill-backed give-up kept it (two copies of the same blank, one
        # drifted). Both lanes now build it through the shared
        # _give_up_blank, so an exhausted cell settles NAMING the tool it
        # blamed, not just the cause. FAILS if a lane's blank drops it again.
        prior = CellRunResult(
            declined_cause=StoredCellState.TRANSIENT,
            blamed_tool="web_search",
            tools={"web_search": "rate_limited"},
        )
        task = NodeRun(result=prior.model_dump())
        blank = ProcessNodeRun(task=task, worker_id=WORKER)._give_up_blank()
        self.assertEqual(blank.blamed_tool, "web_search")
        self.assertEqual(blank.declined_cause, StoredCellState.TRANSIENT)
        self.assertEqual(blank.tools, {"web_search": "rate_limited"})


@override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
class ConsumeLoopResilienceTests(TestCase):
    def test_a_database_error_recovers_the_connection_and_does_not_crash(self) -> None:
        # The one process draining the queue must survive a DB bounce
        # (compose sets no restart policy). A DatabaseError out of the work
        # recovers the connection and, in once mode, returns cleanly rather
        # than propagating; the offset is left uncommitted so the message
        # redelivers and the reclaim cron recovers any PROCESSING task.
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = json.dumps({"task_id": "01TASKAAAAAAAAAAAAAAAAAAAA"}).encode()
        consumer = MagicMock()
        consumer.poll.return_value = msg
        with (
            patch("confluent_kafka.Consumer", return_value=consumer),
            patch(
                "lists.operations.consume_node_runs.handle_node_run",
                side_effect=DatabaseError("server closed the connection unexpectedly"),
            ),
            patch("lists.operations.consume_node_runs.connection.close") as close,
            patch("lists.operations.consume_node_runs._touch_heartbeat"),
        ):
            NodeRunConsumer(worker_id=WORKER, stop=threading.Event()).run(once=True)  # must not raise
        close.assert_called_once()  # the broken connection was dropped for a fresh one
        consumer.commit.assert_not_called()  # offset left uncommitted -> redelivers
        consumer.close.assert_called_once()

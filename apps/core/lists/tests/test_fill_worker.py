"""The MANUAL fill lane end to end on the shared task state machine:
admission (tasks born READY) -> the provisioner publishes -> the shared
consumer's handle_fill_task claims, runs, and lands. The model is a
scripted FunctionModel through the patched model seam; everything else
(lists, admission, the state machine, landing, derived counters) is
real.

This module also HOSTS the shared model fixtures and `_patches` the
autofill and test-kind suites import, so the one mock boundary (the
model seam) is defined once.

TransactionTestCase, NOT TestCase: the consumer opens its own DB work
and admission takes select_for_update, which would deadlock against a
test-wrapping transaction.

Run: DJANGO_ENV=test uv run python apps/core/manage.py test lists.tests.test_fill_worker
"""

from __future__ import annotations

import threading
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.providers import ModelUnavailable
from agents.tools.registry import UnknownTool
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools

from ..constants import (
    FILL_ROW_ATTEMPTS,
    FillFailureCode,
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask, List, ListRow
from ..operations.consume_fill_tasks import handle_fill_task
from ..operations.provision import FillProvisionOperation
from ..services.fill_admission import FillAdmissionService
from ..services.fills import FillService, derive_counters
from ..services.lists import ListService

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"
WORKER = "test:consumer"


def _answer(info: AgentInfo, value: str, *, confidence: float = 0.95, reason: str = "") -> ModelResponse:
    """A COMPLIANT answer: the schema requires every field, so a
    scripted model has to send the whole shape like a real one."""
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=info.output_tools[0].name,
                args={
                    "answer": value,
                    "answer_bwr_confidence": confidence,
                    "answer_bwr_confidence_reason": reason,
                },
            )
        ]
    )


def answering_model(value_for_row) -> FunctionModel:
    """Answers each prompt via value_for_row(prompt text)."""

    def fn(messages, info: AgentInfo):
        return _answer(info, value_for_row(messages[0].parts[-1].content))

    return FunctionModel(fn)


def unsure_model(value: str, *, confidence: float, reason: str) -> FunctionModel:
    """Answers with a confidence the floor will reject."""

    def fn(messages, info: AgentInfo):
        return _answer(info, value, confidence=confidence, reason=reason)

    return FunctionModel(fn)


def throttling_model() -> FunctionModel:
    def fn(messages, info: AgentInfo):
        raise ModelHTTPError(status_code=429, model_name="scripted", body=None)

    return FunctionModel(fn)


def flaky_then_answering_model(value: str) -> FunctionModel:
    """429s the FIRST call, answers every later one."""
    state = {"first": True}

    def fn(messages, info: AgentInfo):
        if state["first"]:
            state["first"] = False
            raise ModelHTTPError(status_code=429, model_name="scripted", body=None)
        return _answer(info, value)

    return FunctionModel(fn)


def _patches(model):
    """The one mock boundary: the model seam. The claim-time gate in the
    fill processor and the runtime's own resolve both read model_for, so
    both are patched to the scripted model."""
    stack = ExitStack()
    stack.enter_context(patch("lists.services.fill_processing.processor.model_for", return_value=model))
    stack.enter_context(patch("agents.runtime.answer.answerer.model_for", return_value=model))
    return stack


def quick_config() -> AgentConfig:
    return AgentConfig(
        prompt="Find the answer for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="scripted",
        tools=AgentTools(),
        outputs=[AgentOutput(key="answer", label="Answer", type="text")],
    )


def counting(fill: Fill) -> dict:
    """The nonzero DERIVED counters, read the way the wire builds them."""
    counters = derive_counters(fill)
    return {key: value for key, value in counters.model_dump().items() if value}


class ManualFillTestCase(TransactionTestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        with patch("lists.services.fill_admission.base.model_for"):
            self.fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(self.sheet.id),
                config=quick_config(),
                confirmed_row_count=2,
            )

    def run_fill(self, model, *, fill: Fill | None = None, passes: int = 1) -> None:
        """Drive a fill's tasks through the shared consumer directly (no
        broker): each pass claims and runs every non-terminal task in
        sheet order, making parked tasks DUE between passes so a retry
        comes round the way advancing the clock means it does."""
        fill = fill or self.fill
        with _patches(model):
            for pass_number in range(passes):
                if pass_number:
                    FillTask.objects.filter(fill_run_id=str(fill.id), not_before__isnull=False).update(
                        not_before=timezone.now()
                    )
                ids = list(
                    FillTask.objects.filter(
                        fill_run_id=str(fill.id),
                        status__in=(FillTaskStatus.READY, FillTaskStatus.QUEUED),
                    )
                    .order_by("position")
                    .values_list("id", flat=True)
                )
                for task_id in ids:
                    handle_fill_task(str(task_id), WORKER)

    def _statuses(self, fill: Fill | None = None) -> set[str]:
        fill = fill or self.fill
        return {t.status for t in FillTask.objects.filter(fill_run_id=str(fill.id))}

    def test_walks_the_sheet_and_completes(self) -> None:
        self.run_fill(answering_model(lambda prompt: "found: " + prompt.split()[-1]))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        self.assertEqual(rows[0].data["answer"], "found: acme.com")
        self.assertEqual(rows[1].data["answer"], "found: example.io")
        self.assertEqual(self._statuses(), {FillTaskStatus.DONE})
        # An answered cell carries a FILLED record beside its value, with
        # this fill's run id (the derived counters read off it).
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.FILLED})
        self.assertEqual({c.fill_run_id for c in FillCellState.objects.all()}, {str(self.fill.id)})
        self.assertEqual(counting(self.fill), {"attempted": 2, "filled": 2})

    def test_blank_answers_land_diagnosed_not_written(self) -> None:
        self.run_fill(answering_model(lambda prompt: ""))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        for row in self.lists.rows_page(self.sheet, after_position=0, limit=10):
            self.assertNotIn("answer", row.data)
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.NO_EVIDENCE})
        self.assertEqual(counting(self.fill), {"attempted": 2, "blank": 2})

    def test_a_dropped_answer_is_preserved_for_audit(self) -> None:
        self.run_fill(unsure_model("Acme Holdings", confidence=0.62, reason="no record states the parent"))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        for row in self.lists.rows_page(self.sheet, after_position=0, limit=10):
            self.assertNotIn("answer", row.data)
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.UNVERIFIED})
        for task in FillTask.objects.filter(fill_run_id=str(self.fill.id)):
            self.assertEqual(
                task.result["assessments"]["answer"],
                {"confidence": 0.62, "reason": "no record states the parent", "dropped": "Acme Holdings"},
            )

    def test_a_model_that_omits_a_required_field_is_unparseable_not_silent(self) -> None:
        def fn(messages, info: AgentInfo):
            return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"answer": "found"})])

        self.run_fill(FunctionModel(fn))
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.UNPARSEABLE})

    def test_throttled_rows_park_and_are_owed_until_the_fill_stops(self) -> None:
        # A 429 is infrastructure, not an answer: the task parks back to
        # READY behind a real backoff, nothing is diagnosed, the cell
        # goes on shimmering, and the fill reads RUNNING (flipped on the
        # first claim). transient is a DERIVED count of parked rows.
        self.run_fill(throttling_model())
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.RUNNING)
        parked = FillTask.objects.filter(fill_run_id=str(self.fill.id), status=FillTaskStatus.READY, parked=True)
        self.assertEqual(parked.count(), 2)
        self.assertTrue(all(task.not_before is not None for task in parked))
        self.assertFalse(FillCellState.objects.exists())
        self.assertEqual(derive_counters(self.fill).transient, 2)

    def test_cancelling_a_fill_abandons_its_parked_rows(self) -> None:
        # Parked (READY) tasks are swept to ABANDONED on cancel, so the
        # derived transient count drops to zero and nothing shimmers.
        self.run_fill(throttling_model())
        self.fill.refresh_from_db()
        self.assertEqual(derive_counters(self.fill).transient, 2)
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.CANCELLED)
        self.assertEqual(self._statuses(), {FillTaskStatus.ABANDONED})
        self.assertEqual(derive_counters(self.fill).transient, 0)

    def test_retry_then_fill_clears_the_transient_count(self) -> None:
        self.run_fill(flaky_then_answering_model("found"), passes=2)
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        self.assertEqual(counting(self.fill), {"attempted": 2, "filled": 2})
        self.assertEqual(self._statuses(), {FillTaskStatus.DONE})

    def test_exhausted_retries_land_a_diagnosed_terminal_blank(self) -> None:
        # One row burning every attempt is TERMINAL: the give-up at the
        # cap lands the last park's cause (TRANSIENT), so the cell reads
        # transient and the counters reach the consented count.
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        with patch("lists.services.fill_admission.base.model_for"):
            solo = self.lists.create(
                owner_id=USER,
                label="Solo",
                columns=[{"key": "company", "label": "Company", "type": "text"}],
                origin="manual",
            )
            self.lists.add_rows(solo, [{"company": "acme.com"}])
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(solo.id), config=quick_config(), confirmed_row_count=1
            )
        self.run_fill(throttling_model(), fill=fill, passes=FILL_ROW_ATTEMPTS + 1)
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        self.assertEqual(counting(fill), {"attempted": 1, "blank": 1})
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        self.assertEqual(task.status, FillTaskStatus.DONE)
        self.assertEqual(task.attempts, FILL_ROW_ATTEMPTS + 1)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.TRANSIENT)

    def test_an_unrunnable_config_fails_the_fill_and_settles_the_task(self) -> None:
        # The claim-time model gate is the surviving config-tier FAILED
        # writer: a run that cannot resolve its model fails the WHOLE
        # fill (it fails every row identically) and settles the task
        # without spending.
        with (
            patch("lists.services.fill_processing.processor.model_for", side_effect=ModelUnavailable("source closed")),
            patch("agents.runtime.answer.answerer.model_for"),
        ):
            first_task = (
                FillTask.objects.filter(fill_run_id=str(self.fill.id))
                .order_by("position")
                .values_list("id", flat=True)[0]
            )
            handle_fill_task(str(first_task), WORKER)
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.FAILED)
        self.assertEqual(self.fill.error_code, FillFailureCode.MODEL_UNRUNNABLE)
        self.assertIn("source closed", self.fill.error_message)
        self.assertFalse(FillCellState.objects.exists())

    def test_a_retired_tool_fails_the_fill_at_run_not_crash_loops(self) -> None:
        # A retired tool surfaces only inside run_cell (model_for does not
        # resolve tools), so the claim-time model gate cannot catch it. The
        # run_cell wrap treats it as the same config-tier fail: the WHOLE
        # fill fails loudly and the task settles, instead of the row
        # crash-looping to its attempt cap on every reclaim.
        with (
            patch("lists.services.fill_processing.processor.model_for"),  # model resolves; the tool is the problem
            patch(
                "lists.services.fill_processing.processor.run_cell",
                side_effect=UnknownTool("web_search retired"),
            ),
        ):
            first_task = (
                FillTask.objects.filter(fill_run_id=str(self.fill.id))
                .order_by("position")
                .values_list("id", flat=True)[0]
            )
            outcome = handle_fill_task(str(first_task), WORKER)
        self.assertEqual(outcome, "done")
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.FAILED)
        self.assertEqual(self.fill.error_code, FillFailureCode.MODEL_UNRUNNABLE)
        self.assertIn("web_search retired", self.fill.error_message)
        self.assertEqual(FillTask.objects.get(id=first_task).status, FillTaskStatus.DONE)

    def test_a_missing_row_closes_its_task_and_the_fill_goes_on(self) -> None:
        gone = ListRow.objects.filter(list_id=str(self.sheet.id)).order_by("position").first()
        ListRow.objects.filter(id=gone.id).delete()
        self.run_fill(answering_model(lambda prompt: "found"))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        self.assertEqual(counting(self.fill), {"attempted": 1, "filled": 1})
        statuses = {t.row_id: t.status for t in FillTask.objects.filter(fill_run_id=str(self.fill.id))}
        self.assertEqual(statuses[str(gone.id)], FillTaskStatus.ROW_MISSING)
        self.assertEqual(set(statuses.values()), {FillTaskStatus.ROW_MISSING, FillTaskStatus.DONE})
        self.assertFalse(FillCellState.objects.filter(row_id=str(gone.id)).exists())

    def test_a_purged_list_cancels_the_fill(self) -> None:
        ListRow.objects.filter(list_id=str(self.sheet.id)).delete()
        List.objects.filter(id=self.sheet.id).delete()
        self.run_fill(answering_model(lambda prompt: "found"))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.CANCELLED)
        self.assertFalse(FillCellState.objects.exists())

    def test_cancelled_run_stops_without_spending(self) -> None:
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        calls: list[str] = []
        self.run_fill(answering_model(lambda prompt: calls.append(prompt) or "x"))
        self.assertEqual(calls, [])
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.CANCELLED)

    def test_the_answer_an_occupied_cell_refused_is_kept(self) -> None:
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        self.lists.write_cells(str(self.sheet.id), str(rows[0].id), {"answer": "mine, typed by hand"})
        self.run_fill(answering_model(lambda prompt: "what the model found"))
        task = FillTask.objects.get(fill_run_id=str(self.fill.id), row_id=str(rows[0].id))
        row = ListRow.objects.get(id=rows[0].id)
        self.assertEqual(row.data["answer"], "mine, typed by hand")
        self.assertEqual(task.result["cells"]["answer"], "what the model found")


@override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
class ProvisionerTests(ManualFillTestCase):
    """The manual provisioner: it publishes each live fill's READY tasks
    to the manual topic and marks them QUEUED. The broker is mocked at
    its client boundary; everything else is real."""

    def _run_provisioner(self, producer):
        with patch("confluent_kafka.Producer", return_value=producer):
            FillProvisionOperation(worker_id="test:prov", stop=threading.Event()).run(once=True)

    def test_it_publishes_each_ready_task_and_marks_it_queued(self) -> None:
        producer = MagicMock()
        producer.flush.return_value = 0  # the broker acked
        tasks = list(FillTask.objects.filter(fill_run_id=str(self.fill.id)).order_by("position"))
        self.assertEqual({t.status for t in tasks}, {FillTaskStatus.READY})

        self._run_provisioner(producer)

        self.assertEqual(producer.produce.call_count, len(tasks))
        # ONE flush for the fill's whole page, not one per task: the batched
        # publish (produce the page, flush once) is what lets a single
        # provisioner feed the consumer fleet. FAILS if it regresses to a
        # per-task flush (call_count would be len(tasks)).
        self.assertEqual(producer.flush.call_count, 1)
        args, _kwargs = producer.produce.call_args
        self.assertEqual(args[0], "list.fill.manual")
        for task in tasks:
            task.refresh_from_db()
            self.assertEqual(task.status, FillTaskStatus.QUEUED)
            self.assertIsNotNone(task.queued_at)

    def test_a_test_fill_routes_to_the_isolated_test_topic(self) -> None:
        # A bench TEST fill must NOT ride the manual topic (it would queue
        # behind a wide manual fill the user is not watching); the
        # provisioner routes it to its own lane by kind.
        from ..services.fill_admission import TestFillAdmission

        with patch("lists.services.fill_admission.base.model_for"):
            TestFillAdmission(account_id=ACCOUNT, user_id=USER).admit(
                config=quick_config(), row={"company": "bench.co"}
            )
        producer = MagicMock()
        producer.flush.return_value = 0
        self._run_provisioner(producer)
        topics = {call.args[0] for call in producer.produce.call_args_list}
        self.assertIn("list.fill.test", topics)  # the bench fill's task, isolated
        self.assertIn("list.fill.manual", topics)  # setUp's normal fill still on manual

    def test_a_failed_publish_backs_off_and_leaves_the_tasks_ready(self) -> None:
        producer = MagicMock()
        producer.flush.return_value = 1  # the ack never arrived
        self._run_provisioner(producer)  # backs off, does not raise
        for task in FillTask.objects.filter(fill_run_id=str(self.fill.id)):
            self.assertEqual(task.status, FillTaskStatus.READY)
            self.assertIsNone(task.queued_at)

    def test_a_pass_publishes_at_most_the_batch_and_leaves_the_rest_ready(self) -> None:
        # The per-pass batch bounds how many of a fill's READY tasks ONE
        # pass publishes: batch 1 against 2 READY tasks publishes exactly
        # one and leaves the other READY for the next pass. FAILS if the
        # limit is dropped (both would publish in the pass).
        producer = MagicMock()
        producer.flush.return_value = 0
        with (
            patch("confluent_kafka.Producer", return_value=producer),
            patch("lists.operations.provision.fill.FILL_PUBLISH_BATCH", 1),
        ):
            FillProvisionOperation(worker_id="test:prov", stop=threading.Event())._one_pass()
        self.assertEqual(producer.produce.call_count, 1)
        self.assertEqual(
            sorted(FillTask.objects.filter(fill_run_id=str(self.fill.id)).values_list("status", flat=True)),
            [FillTaskStatus.QUEUED, FillTaskStatus.READY],
        )

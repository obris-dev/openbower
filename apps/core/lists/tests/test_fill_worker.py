"""The worker end to end: admission -> `fill_worker --once` -> cells
in the sheet, outcomes diagnosed, fill terminal. The model is a
scripted FunctionModel through the patched model_for seam (the
runtime tests' precedent); everything else is real.
TransactionTestCase, NOT TestCase: the worker's row threads open their
own DB connections, which would deadlock against a test-wrapping
transaction's uncommitted rows and held locks."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch

from django.core.management import call_command
from django.test import TransactionTestCase
from django.utils import timezone
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from openbower_kernel.provider_config import ProviderSpec, canonical_base, make_source
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools

from ..constants import (
    CONSECUTIVE_TRANSIENT_LIMIT,
    FILL_CONCURRENCY_HOSTED_START,
    FILL_ROW_ATTEMPTS,
    FillFailureCode,
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask, ListRow
from ..operations.fill_worker import FillWorkerOperation, _FillState
from ..services.fill_admission import FillAdmissionService
from ..services.fill_queue import FillQueueService
from ..services.fills import FillService
from ..services.lists import ListService

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"


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


# The default test source: one row in flight, so row order is
# deterministic. Built through make_source, never a SourceConfig
# literal: passing the derived `canonical` by hand is the one thing
# that constructor exists to prevent, and a fixture that does it is a
# fixture that can disagree with production.
PINNED_SOURCE = make_source(ProviderSpec.OPENAI_COMPATIBLE.value, base_url="http://localhost:11434/v1", concurrency=1)
# A source the AIMD controller can actually MOVE inside: the vendor
# origin (so canonical, starting at FILL_CONCURRENCY_HOSTED_START) with
# no declared ceiling (so it may climb to MAX_FILL_CONCURRENCY). Every
# other worker test pins the window to (1, 1), where the controller is
# inert: min(2, 1) and max(1, 0) are both 1, so no climb, no halving,
# and no epoch is observable.
WIDE_SOURCE = make_source(
    ProviderSpec.OPENAI_COMPATIBLE.value, base_url=canonical_base(ProviderSpec.OPENAI_COMPATIBLE.value), api_key="k"
)


def _patches(model, *, source=None):
    """The one mock boundary: the model seam. Rows resolve their OWN
    model (a run closes its client; sharing one killed sibling rows
    against real providers), so both seams are patched."""
    stack = ExitStack()
    stack.enter_context(patch("lists.operations.fill_worker.model_for", return_value=model))
    stack.enter_context(patch("agents.runtime.cell.model_for", return_value=model))
    stack.enter_context(patch("lists.operations.fill_worker.source_config", return_value=source or PINNED_SOURCE))
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


COUNTER_KEYS = ("attempted", "filled", "blank", "transient")
PACE_KEYS = ("row_seconds", "search_wait_seconds", "concurrency_point")


def counting(fill: Fill) -> dict:
    """The deterministic counters, read off the fill's own columns.
    The pace accumulators carry wall-clock values a pin cannot
    equal-match, so their SHAPE is asserted here and their values are
    not."""
    for key in PACE_KEYS:
        assert isinstance(getattr(fill, key), int)
    return {key: getattr(fill, key) for key in COUNTER_KEYS if getattr(fill, key)}


class WorkerTestCase(TransactionTestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT, user_id=USER)
        self.sheet = self.lists.create(
            label="Prospects", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        with patch("lists.services.fill_admission.model_for"):
            self.fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(self.sheet.id),
                config=quick_config(),
                confirmed_row_count=2,
            )

    def run_worker(self, model, *, passes: int = 1) -> None:
        """`passes` runs the command repeatedly, making every parked
        task DUE in between. A retry now backs off in real time, so a
        single --once pass exits before it comes round; advancing the
        clock is what a test means by "and then it retried"."""
        for pass_number in range(passes):
            if pass_number:
                FillTask.objects.filter(not_before__isnull=False).update(not_before=timezone.now())
            self._run_once(model)

    def run_supervisor(self, model, *, passes: int, source=None):
        """ONE long-lived supervisor across several passes, which is
        what a deployed worker is. The across-row breakers live in that
        process, so a test that re-invoked the command each pass would
        hand the provider a fresh count every time and never trip
        them."""
        with self._patched(model, source=source), ThreadPoolExecutor(max_workers=4) as pool:
            supervisor = FillWorkerOperation(queue=FillQueueService(worker_id="test:sup"), stop=threading.Event())
            for pass_number in range(passes):
                if pass_number:
                    FillTask.objects.filter(not_before__isnull=False).update(not_before=timezone.now())
                supervisor._one_pass(pool)
                # Drain what this pass submitted before advancing: a
                # pass that left rows running would leave the counts
                # depending on how fast the pool happened to be.
                while any(state.in_flight for state in supervisor._states.values()):
                    supervisor._harvest()
            return supervisor

    def _patched(self, model, *, source=None):
        return _patches(model, source=source)

    def _run_once(self, model, *, source=None) -> None:
        with self._patched(model, source=source):
            call_command("fill_worker", "--once")

    def test_walks_the_sheet_and_completes(self) -> None:
        self.run_worker(answering_model(lambda prompt: "found: " + prompt.split()[-1]))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        self.assertEqual(rows[0].data["answer"], "found: acme.com")
        self.assertEqual(rows[1].data["answer"], "found: example.io")
        self.assertEqual({t.status for t in FillTask.objects.filter(fill_id=str(self.fill.id))}, {FillTaskStatus.DONE})
        # An answered cell carries a FILLED record beside its value:
        # that record is what makes the per-column count an indexed
        # read instead of a scan of the sheet.
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.FILLED})
        self.assertEqual(counting(self.fill), {"attempted": 2, "filled": 2})
        self.assertIsNotNone(self.fill.heartbeat_at)

    def test_blank_answers_land_diagnosed_not_written(self) -> None:
        self.run_worker(answering_model(lambda prompt: ""))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        for row in rows:
            self.assertNotIn("answer", row.data)
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.NO_EVIDENCE})
        self.assertEqual(counting(self.fill), {"attempted": 2, "blank": 2})

    def test_a_dropped_answer_is_preserved_for_audit(self) -> None:
        # A blank cell has to stay explicable. The log line that used to
        # be the only record truncates, is not queryable per row, and
        # dies with the process; tuning the floor needs the REJECTED
        # distribution, which exists nowhere else.
        self.run_worker(unsure_model("Acme Holdings", confidence=0.62, reason="no record states the parent"))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        for row in self.lists.rows_page(self.sheet, after_position=0, limit=10):
            self.assertNotIn("answer", row.data)
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.UNVERIFIED})
        for task in FillTask.objects.filter(fill_id=str(self.fill.id)):
            self.assertEqual(
                task.result["assessments"]["answer"],
                {"confidence": 0.62, "reason": "no record states the parent", "dropped": "Acme Holdings"},
            )

    def test_a_model_that_omits_a_required_field_is_unparseable_not_silent(self) -> None:
        # The schema requires every field, so an omission cannot land
        # as a default. It fails validation, exhausts the one retry,
        # and diagnoses UNPARSEABLE: the model spoke but never in the
        # output type, which is a signal to change models rather than
        # a threshold to tune.
        def fn(messages, info: AgentInfo):
            return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"answer": "found"})])

        self.run_worker(FunctionModel(fn))
        self.assertEqual({c.state for c in FillCellState.objects.all()}, {StoredCellState.UNPARSEABLE})

    def test_a_landed_answer_records_its_score_without_a_dropped_value(self) -> None:
        self.run_worker(answering_model(lambda prompt: "found"))
        for outcome in FillTask.objects.filter(fill_id=str(self.fill.id)):
            self.assertEqual(outcome.result["assessments"]["answer"]["confidence"], 0.95)
            self.assertNotIn("dropped", outcome.result["assessments"]["answer"])

    def test_throttled_rows_park_and_are_owed_until_the_fill_stops(self) -> None:
        # A 429 is infrastructure, not an answer. The task stays QUEUED
        # behind a real backoff, nothing is diagnosed, and the cell goes
        # on shimmering because it is still owed.
        self.run_worker(throttling_model())
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.RUNNING)
        parked = FillTask.objects.filter(fill_id=str(self.fill.id), status=FillTaskStatus.QUEUED)
        self.assertTrue(parked.exists())
        self.assertTrue(all(task.not_before is not None for task in parked.filter(attempts__gt=0)))
        self.assertFalse(FillCellState.objects.exists())
        # Both rows parked: `transient` is a CURRENT count of rows in
        # retry, bumped once per row on its first park.
        self.assertEqual(self.fill.transient, 2)

    def test_a_tripped_breaker_fails_the_fill_and_abandons_its_queue(self) -> None:
        # The breaker itself is a pure object (that is why it was split
        # out); what this pins is the SUPERVISOR's response to it, which
        # is the part with a fill and a queue to touch.
        supervisor = FillWorkerOperation(queue=FillQueueService(worker_id="test:sup"), stop=threading.Event())
        with self._patched(answering_model(lambda prompt: "found")), ThreadPoolExecutor(max_workers=1) as pool:
            state = supervisor._admit(self.fill)
            for _ in range(CONSECUTIVE_TRANSIENT_LIMIT):
                state.breakers.row_finished(transient=True, searches=[], at_floor=True)
            self.assertIsNotNone(state.breakers.tripped)
            supervisor._serve(self.fill, pool)
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.FAILED)
        self.assertEqual(self.fill.error_code, "provider_throttled")
        # Rendered verbatim in the chip: names only steps that exist in
        # this slice (no refill gesture yet), and what was kept.
        self.assertEqual(
            self.fill.error_message,
            "The model provider is throttling this fill; it stopped rather than blanking the column. "
            "Filled cells are kept.",
        )
        # Stopping ABANDONS what the fill never spent, which is the
        # record a later resume reads, and diagnoses nothing: a parked
        # cell was never answered, so there is no verdict to write.
        self.assertEqual(
            {t.status for t in FillTask.objects.filter(fill_id=str(self.fill.id))}, {FillTaskStatus.ABANDONED}
        )
        self.assertFalse(FillCellState.objects.exists())

    def test_retry_then_fill_releases_the_transient_counter(self) -> None:
        # A row that parks transient and later fills must not read as
        # "currently parked" forever: the wire's transient field is a
        # CURRENT count, so the terminal write releases it.
        self.run_worker(flaky_then_answering_model("found"), passes=2)
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        self.assertEqual(counting(self.fill), {"attempted": 2, "filled": 2})
        self.assertEqual({t.status for t in FillTask.objects.filter(fill_id=str(self.fill.id))}, {FillTaskStatus.DONE})

    def test_cancelling_a_fill_releases_its_parked_rows(self) -> None:
        # The abandon sweep is the THIRD terminal writer. A parked task
        # leaves QUEUED either by completing (which decrements) or by
        # being abandoned here, and try_finish refuses to complete a
        # fill while anything is queued, so there is no other exit.
        # Missing it left the gauge stuck high on the fills most likely
        # to have parked rows: the throttle breaker fails a fill
        # precisely when they are.
        self.run_worker(throttling_model(), passes=1)
        self.fill.refresh_from_db()
        parked = FillTask.objects.filter(fill_id=str(self.fill.id), parked=True).count()
        self.assertGreater(parked, 0)
        self.assertEqual(self.fill.transient, parked)

        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.CANCELLED)
        self.assertEqual(self.fill.transient, 0)

    def test_a_failed_fill_releases_them_too(self) -> None:
        # Same sweep, reached through the breaker rather than a cancel,
        # which is the likelier door: PROVIDER_THROTTLED trips when
        # rows are transient by definition.
        self.run_worker(throttling_model(), passes=1)
        self.fill.refresh_from_db()
        self.assertGreater(self.fill.transient, 0)
        FillQueueService(worker_id="probe").fail_fill(
            str(self.fill.id), code=FillFailureCode.PROVIDER_THROTTLED, message="throttled"
        )
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.FAILED)
        self.assertEqual(self.fill.transient, 0)

    def test_giving_up_on_a_row_that_never_parked_leaves_transient_alone(self) -> None:
        # The give-up path is the OTHER terminal writer, and it read
        # `attempts > 1` to decide whether a park had counted this row.
        # It is reached only past the attempt cap, so that test could
        # never be false and it decremented unconditionally: a row
        # whose thread dies every pass never parks, never increments,
        # and still got decremented at the cap.
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        with patch("lists.services.fill_admission.model_for"):
            solo = self.lists.create(
                label="Solo", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
            )
            self.lists.add_rows(solo, [{"company": "acme.com"}])
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(solo.id), config=quick_config(), confirmed_row_count=1
            )
        # Burn every attempt WITHOUT parking: claim and release, the
        # dead-worker-thread path, which is what a redeploy loop does.
        queue = FillQueueService(worker_id="probe")
        for _ in range(FILL_ROW_ATTEMPTS):
            claimed = queue.claim_batch(fill, free_slots=1)
            queue.release_lease(claimed.tasks[0])
        task = FillTask.objects.get(fill_id=str(fill.id))
        self.assertEqual(task.attempts, FILL_ROW_ATTEMPTS)
        self.assertFalse(task.parked)

        # The next claim is one too many, so the worker gives up.
        self.run_worker(answering_model(lambda prompt: "answered"), passes=1)
        fill.refresh_from_db()
        self.assertEqual(fill.transient, 0)
        self.assertGreaterEqual(fill.transient, 0)

    def test_a_throttled_burst_halves_a_movable_point_once(self) -> None:
        # Every other worker test pins the source to concurrency=1,
        # which makes the window (1, 1) and the controller INERT: no
        # climb, no halving, no epoch. Nothing here could observe the
        # per-event shedding, so reverting it would keep the suite
        # green. This runs against a source the point can move inside.
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        with patch("lists.services.fill_admission.model_for"):
            wide = self.lists.create(
                label="Wide", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
            )
            self.lists.add_rows(wide, [{"company": f"c{n}.com"} for n in range(4)])
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(wide.id), config=quick_config(), confirmed_row_count=4
            )
        supervisor = self.run_supervisor(throttling_model(), passes=1, source=WIDE_SOURCE)
        controller = supervisor._states[str(fill.id)].controller
        # The window is movable here, so the point STARTED at the
        # hosted start; a burst that throttled every row in flight
        # halved it ONCE, not once per row (which would floor it at 1).
        self.assertEqual(controller.current(), FILL_CONCURRENCY_HOSTED_START // 2)
        self.assertGreater(FILL_CONCURRENCY_HOSTED_START // 2, 1)

    def test_a_reclaimed_row_that_never_parked_leaves_transient_alone(self) -> None:
        # `attempts` increments at CLAIM, so a released lease or a
        # stale reclaim raises it with no park behind it. Inferring
        # "was parked" from that number made the terminal write
        # decrement a gauge nobody had incremented, and transient went
        # permanently negative. The park marker is not_before, which
        # only park_task sets.
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        with patch("lists.services.fill_admission.model_for"):
            solo = self.lists.create(
                label="Solo", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
            )
            self.lists.add_rows(solo, [{"company": "acme.com"}])
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(solo.id), config=quick_config(), confirmed_row_count=1
            )
        # Claim once and RELEASE, the dead-worker-thread path: the
        # attempt is counted, nothing was ever parked.
        queue = FillQueueService(worker_id="probe")
        claimed = queue.claim_batch(fill, free_slots=1)
        queue.release_lease(claimed.tasks[0])
        self.assertEqual(FillTask.objects.get(fill_id=str(fill.id)).attempts, 1)
        self.assertIsNone(FillTask.objects.get(fill_id=str(fill.id)).not_before)

        # Now let the worker run it to a clean terminal write.
        self.run_worker(answering_model(lambda prompt: "answered"), passes=1)
        fill.refresh_from_db()
        self.assertGreaterEqual(fill.transient, 0)
        self.assertEqual(fill.transient, 0)

    def test_exhausted_retries_count_as_terminal_blanks(self) -> None:
        # One row burning every attempt is TERMINAL (a transient
        # blank): it lands in attempted/blank and leaves transient at
        # zero, so the chip's totals reach the consented count.
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        with patch("lists.services.fill_admission.model_for"):
            solo = self.lists.create(
                label="Solo", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
            )
            self.lists.add_rows(solo, [{"company": "acme.com"}])
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(solo.id), config=quick_config(), confirmed_row_count=1
            )
        # One row, so the across-row breaker never trips: it burns its
        # own attempts instead, and the claim past the cap gives up.
        self.run_worker(throttling_model(), passes=FILL_ROW_ATTEMPTS + 1)
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        self.assertEqual(counting(fill), {"attempted": 1, "blank": 1})
        task = FillTask.objects.get(fill_id=str(fill.id))
        # Given up on at the cap, by the claim that read one attempt
        # too many. TRANSIENT, not model_error: attempts only climb
        # through parks and lost leases, so reaching the cap means the
        # provider kept being unreachable, which is what the cell's
        # copy says. It is retryable either way, so a refill re-targets
        # it; the difference is whether the user is told the model
        # failed or the provider did. This is the ONLY writer of the
        # terminal transient blank.
        self.assertEqual(task.status, FillTaskStatus.DONE)
        self.assertEqual(task.attempts, FILL_ROW_ATTEMPTS + 1)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.TRANSIENT)

    def test_a_drained_but_live_job_completes_on_the_next_pass(self) -> None:
        # A worker killed between its last terminal write and
        # try_finish (SIGTERM on the final rows) leaves a live fill
        # with every row terminal: the next pass must flip it
        # COMPLETE, never skip it forever.
        Fill.objects.filter(id=self.fill.id).update(status=FillStatus.RUNNING)
        FillTask.objects.filter(fill_id=str(self.fill.id)).update(status=FillTaskStatus.DONE)
        self.run_worker(answering_model(lambda prompt: "unused"))
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)

    def test_cancelled_job_stops_without_spending(self) -> None:
        FillService(account_id=ACCOUNT).cancel(str(self.fill.id))
        calls = []
        self.run_worker(answering_model(lambda prompt: calls.append(prompt) or "x"))
        self.assertEqual(calls, [])
        self.fill.refresh_from_db()
        self.assertEqual(self.fill.status, FillStatus.CANCELLED)

    def test_type_mismatch_diagnosed_when_shape_refuses(self) -> None:
        with patch("lists.services.fill_admission.model_for"):
            other = self.lists.create(label="Nums", columns=[], origin="manual")
            self.lists.add_rows(other, [{"company": "acme.com"}])
            config = AgentConfig(
                prompt="Count for {{company}}",
                provider="openai_compatible",
                source="ollama",
                model="scripted",
                tools=AgentTools(),
                outputs=[AgentOutput(key="answer", label="Answer", type="number")],
            )
            FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(other.id), config=config, confirmed_row_count=1
            )
        # Complete the first fill so --once reaches the second.
        self.run_worker(answering_model(lambda prompt: "not a number" if "Count" in prompt else "fine"))
        row = self.lists.rows_page(other, after_position=0, limit=1)[0]
        self.assertNotIn("answer", row.data)
        self.assertEqual(
            {c.state for c in FillCellState.objects.filter(row_id=str(row.id))}, {StoredCellState.TYPE_MISMATCH}
        )


class FairnessTests(WorkerTestCase):
    """Every live fill advances in every pass. A wide fill must not be
    able to hold the worker for the length of its own run, since the
    fill next to it belongs to a different account."""

    def _second_sheet(self, account: str, rows: int) -> str:
        lists = ListService(account_id=account, user_id=USER)
        sheet = lists.create(
            label="Theirs", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        lists.add_rows(sheet, [{"company": f"c{n}.io"} for n in range(rows)])
        with patch("lists.services.fill_admission.model_for"):
            fill = FillAdmissionService(account_id=account, user_id=USER).admit(
                list_id=str(sheet.id), config=quick_config(), confirmed_row_count=rows
            )
        return str(fill.id)

    def test_a_wide_fill_does_not_starve_a_later_one(self) -> None:
        # The starvation fix, asserted on INTERLEAVING rather than on
        # completion: the old loop also finished both fills, but only
        # one after the other, so a 25k-row fill held the worker for
        # its entire run. Rows of the two fills must overlap in time.
        other_job_id = self._second_sheet("01ACCOUNTBBBBBBBBBBBBBBBBB", rows=4)
        seen: list[str] = []

        def answer(prompt: str) -> str:
            # Row data carries the sheet apart: setUp's rows are
            # acme/example, the second sheet's are cN.io.
            seen.append("theirs" if ".io" in prompt and prompt.rstrip().endswith(".io") and "c" in prompt else "mine")
            return "found"

        self.run_worker(answering_model(answer))

        self.fill.refresh_from_db()
        other = Fill.objects.get(id=other_job_id)
        self.assertEqual(self.fill.status, FillStatus.COMPLETE)
        self.assertEqual(other.status, FillStatus.COMPLETE)
        mine = [i for i, who in enumerate(seen) if who == "mine"]
        theirs = [i for i, who in enumerate(seen) if who == "theirs"]
        self.assertTrue(mine and theirs, seen)
        # Neither fill runs entirely before the other: each has a row
        # that ran after one of the other's. Under one-fill-at-a-time
        # this is false by construction.
        self.assertTrue(max(mine) > min(theirs) and max(theirs) > min(mine), seen)

    def test_every_live_fill_is_offered_each_pass(self) -> None:
        # The interleave itself, at the seam: live_jobs hands back BOTH
        # fills, oldest first, so no fill can be reached only after
        # another finishes.
        other_job_id = self._second_sheet("01ACCOUNTCCCCCCCCCCCCCCCCC", rows=1)
        live = [str(fill.id) for fill in FillQueueService(worker_id="test:1").live_fills()]
        self.assertEqual(live, sorted([str(self.fill.id), other_job_id]))


class SourceCeilingTests(WorkerTestCase):
    """A declared per-source ceiling is a promise about a BOX, not
    about a fill.

    Each fill runs its own AIMD controller, so two live fills on one
    self-hosted source, each capped at the declared 1, put 2 in flight
    against something that said it handles 1. The window docstring
    defended the multi-WORKER case and never addressed this one, which
    happens at a single worker."""

    def test_two_fills_on_one_source_share_its_declared_ceiling(self) -> None:
        supervisor = FillWorkerOperation(queue=FillQueueService(worker_id="test:sup"), stop=threading.Event())
        with self._patched(answering_model(lambda prompt: "found")):
            mine = supervisor._admit(self.fill)
        self.assertEqual(mine.ceiling, 1)
        # A second fill on the SAME (provider, source), with a row of
        # this one already running.
        theirs = _FillState(self.fill, mine.config, mine.controller, mine.breakers, mine.ceiling)
        supervisor._states["other"] = theirs
        mine.in_flight[object()] = None
        self.assertEqual(supervisor._source_free_slots(theirs), 0)
        mine.in_flight.clear()
        self.assertEqual(supervisor._source_free_slots(theirs), 1)


class OccupiedAnswerTests(WorkerTestCase):
    """write-if-blank protects a value you already had. The model's
    answer still happened, and until now it existed nowhere: the cell
    recorded FILLED as though the fill had written it."""

    def test_the_answer_an_occupied_cell_refused_is_kept(self) -> None:
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        # A value the user already had, in the column the fill targets.
        self.lists.write_cells(str(self.sheet.id), str(rows[0].id), {"answer": "mine, typed by hand"})

        self.run_worker(answering_model(lambda prompt: "what the model found"))

        task = FillTask.objects.get(fill_id=str(self.fill.id), row_id=str(rows[0].id))
        # The user's value stands, untouched.
        row = ListRow.objects.get(id=rows[0].id)
        self.assertEqual(row.data["answer"], "mine, typed by hand")
        # And the model's answer is recoverable rather than lost.
        # The model's own answer, kept where every answer lives now,
        # instead of smuggled into the assessment dict.
        self.assertEqual(task.result["cells"]["answer"], "what the model found")

    def test_a_written_answer_carries_no_occupied_record(self) -> None:
        # The row's own value IS the record when the write lands; a
        # copy beside it would be two records of one fact.
        self.run_worker(answering_model(lambda prompt: "found"))
        for task in FillTask.objects.filter(fill_id=str(self.fill.id)):
            self.assertNotIn("occupied", task.result.get("assessments", {}).get("answer", {}))

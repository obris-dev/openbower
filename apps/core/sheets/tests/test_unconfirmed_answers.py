"""An answer the model scores under the confidence floor: kept on its cell
as unconfirmed (shown, never a value), cleared by whatever writes the cell
next, and explained with its level and reason, off the cell's record, when a
person opens the cell. The floor is the bottom of the HIGH level."""

from __future__ import annotations

import importlib
from unittest.mock import patch

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.confidence import confidence_level
from agents.constants import ANSWER_CONFIDENCE_FLOOR, CONFIDENCE_HIGH_AT
from common.testing import TEST_IDENTITY, login_session
from jobs.services import JobRunner
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from sheets.rows import rows_without_external_ids

from ..cells import AgentCellDetail, AnsweredWrite, CellMismatch, RowVerdict
from ..constants import StoredCellState
from ..models import NodeRun, SheetCellState, SheetRow
from ..operations.set_cells import SetCellsOperation
from ..services.fills import FillService
from ..services.sheets import SheetService
from .fill_helpers import start_fill
from .test_fill_worker import WORKER, model_patches

ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]


def outputs_config(*keys: str) -> AgentConfig:
    return AgentConfig(
        prompt="Find the answer for {{company}}",
        provider="openai_compatible",
        source="ollama",
        model="scripted",
        tools=AgentTools(),
        outputs=[AgentOutput(key=key, label=key.title(), type="text") for key in keys],
    )


def _model(answers: dict[str, tuple[str, float]]) -> FunctionModel:
    """Answers each output with (value, confidence), reason "why <key>"."""

    def fn(messages, info: AgentInfo):
        args: dict = {}
        for key, (value, confidence) in answers.items():
            args[key] = value
            args[f"{key}_bwr_confidence"] = confidence
            args[f"{key}_bwr_confidence_reason"] = f"why {key}"
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])

    return FunctionModel(fn)


class UnconfirmedAnswerTests(TransactionTestCase):
    def _fill(self, keys: tuple[str, ...], answers: dict[str, tuple[str, float]]) -> None:
        self.sheets = SheetService(account_id=ACCOUNT)
        self.sheet = self.sheets.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.sheets.add_rows(self.sheet, rows_without_external_ids([{"company": "acme.com"}]))
        with patch("sheets.services.runnable.model_for"):
            _, fill = start_fill(str(self.sheet.id), account_id=ACCOUNT, user_id=USER, config=outputs_config(*keys))
        JobRunner(worker_id="test:1").tick()
        from ..operations.consume_node_runs import handle_node_run

        with model_patches(_model(answers)):
            for task_id in NodeRun.objects.filter(fill_run_id=str(fill.id)).values_list("id", flat=True):
                handle_node_run(str(task_id), WORKER)
        self.sheet.refresh_from_db()
        self.row = self.sheets.rows_page(self.sheet, limit=1)[0]

    def _record(self, key: str) -> SheetCellState:
        return SheetCellState.objects.get(row_id=str(self.row.id), column_key=key)

    def _wire(self) -> dict:
        states = FillService(account_id=ACCOUNT).cell_states_for_rows(self.sheet, [self.row])
        return {key: state.model_dump() for key, state in states.get(str(self.row.id), {}).items()}

    def test_an_answer_under_the_floor_is_kept_unconfirmed_never_a_value(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.62)})
        self.assertNotIn("answer", self.row.data)
        record = self._record("answer")
        self.assertEqual(record.state, StoredCellState.UNVERIFIED)
        # FAILS if the dropped answer is not kept, or leaks into the row.
        self.assertEqual(
            record.detail, AgentCellDetail(confidence=0.62, reason="why answer", unconfirmed="Acme Holdings")
        )
        self.assertEqual(self._wire()["answer"]["unconfirmed"], "Acme Holdings")

    def test_the_floor_is_the_bottom_of_high(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.85)})
        # FAILS if an answer AT the floor is dropped.
        self.assertEqual(self.row.data["answer"], "Acme Holdings")
        # FAILS if a landed answer's record loses its reason.
        self.assertEqual(self._record("answer").detail, AgentCellDetail(confidence=0.85, reason="why answer"))
        self.assertEqual(ANSWER_CONFIDENCE_FLOOR, 0.85)

    def test_high_starts_at_the_floor(self) -> None:
        # FAILS if the floor or the HIGH band moves alone: the two are
        # separate decisions that agree today, and changing one is a
        # decision about both.
        self.assertEqual(CONFIDENCE_HIGH_AT, ANSWER_CONFIDENCE_FLOOR)

    def test_only_the_dropped_column_is_unverified(self) -> None:
        self._fill(("answer", "other"), {"answer": ("Acme Holdings", 0.62), "other": ("", 0.95)})
        # FAILS if one dropped answer marks every blank of the run.
        self.assertEqual(self._record("answer").state, StoredCellState.UNVERIFIED)
        self.assertEqual(self._record("other").state, StoredCellState.NO_EVIDENCE)
        self.assertIsNone(self._record("other").detail)

    def _backfill(self) -> None:
        # The models as the migration sees them: plain JSON, no contract.
        state = MigrationLoader(connection).project_state(("sheets", "0007_cell_detail"))
        importlib.import_module("sheets.migrations.0007_cell_detail")._backfill(state.apps, None)

    def test_the_backfill_restores_what_each_run_said(self) -> None:
        self._fill(("answer", "other"), {"answer": ("Acme Holdings", 0.62), "other": ("Widgets", 0.97)})
        SheetCellState.objects.update(detail=None)
        self._backfill()
        # FAILS if a cell settled before the detail existed stays silent.
        self.assertEqual(
            self._record("answer").detail,
            AgentCellDetail(confidence=0.62, reason="why answer", unconfirmed="Acme Holdings"),
        )
        self.assertEqual(self._record("other").detail, AgentCellDetail(confidence=0.97, reason="why other"))

    def test_the_backfill_skips_an_edited_value_and_an_unchecked_link(self) -> None:
        self._fill(("answer", "other"), {"answer": ("https://made.up/x", 0.62), "other": ("Widgets", 0.97)})
        SheetRow.objects.filter(id=str(self.row.id)).update(data={**self.row.data, "other": "Gadgets"})
        # As a cell settled before grounding covered a dropped answer.
        SheetCellState.objects.filter(column_key="answer").update(state=StoredCellState.UNVERIFIED)
        SheetCellState.objects.update(detail=None)
        self._backfill()
        # FAILS if the reason lands on a value the run did not write.
        self.assertIsNone(self._record("other").detail)
        # FAILS if a link no grounding checked is offered.
        self.assertEqual(self._record("answer").detail.unconfirmed, "")

    def test_a_person_s_write_clears_the_unconfirmed_answer(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.62)})
        SetCellsOperation(
            account_id=ACCOUNT, target_sheet=self.sheet, row_id=str(self.row.id), cells={"answer": "Acme Holdings"}
        ).run()
        record = self._record("answer")
        self.assertEqual(record.state, StoredCellState.FILLED)
        # FAILS if a restated record keeps what the run said.
        self.assertIsNone(record.detail)

    def test_opening_a_cell_explains_its_answer(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.62)})
        login_session(self.client)
        url = reverse(
            "sheets_cell_detail", kwargs={"id": str(self.sheet.id), "row_id": str(self.row.id), "key": "answer"}
        )
        body = self.client.get(url).json()
        self.assertEqual(body, {"state": "unverified", "level": "low", "reason": "why answer"})

    def test_opening_a_cell_never_reads_the_run(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.62)})
        NodeRun.objects.all().delete()
        login_session(self.client)
        url = reverse(
            "sheets_cell_detail", kwargs={"id": str(self.sheet.id), "row_id": str(self.row.id), "key": "answer"}
        )
        with CaptureQueriesContext(connection) as queries:
            body = self.client.get(url).json()
        # FAILS if the details still come off the fill's runs.
        self.assertEqual(body["reason"], "why answer")
        self.assertFalse(any("sheets_noderun" in q["sql"] for q in queries.captured_queries))

    def test_opening_a_landed_cell_says_how_sure(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.97)})
        login_session(self.client)
        url = reverse(
            "sheets_cell_detail", kwargs={"id": str(self.sheet.id), "row_id": str(self.row.id), "key": "answer"}
        )
        body = self.client.get(url).json()
        self.assertEqual((body["state"], body["level"]), ("filled", "very_high"))

    def test_a_cell_no_writer_reached_answers_empty(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.97)})
        login_session(self.client)
        url = reverse(
            "sheets_cell_detail", kwargs={"id": str(self.sheet.id), "row_id": str(self.row.id), "key": "company"}
        )
        resp = self.client.get(url)
        # FAILS if a plain or never-run cell reads as a failure.
        self.assertEqual((resp.status_code, resp.json()), (200, {"state": None, "level": None, "reason": ""}))

    def test_a_missing_row_or_column_is_not_found(self) -> None:
        self._fill(("answer",), {"answer": ("Acme Holdings", 0.97)})
        login_session(self.client)
        for row_id, key in (("01" + "Z" * 24, "answer"), (str(self.row.id), "nope")):
            with self.subTest(row_id=row_id, key=key):
                url = reverse("sheets_cell_detail", kwargs={"id": str(self.sheet.id), "row_id": row_id, "key": key})
                self.assertEqual(self.client.get(url).status_code, 404)


class ConfidenceLevelTests(SimpleTestCase):
    def test_each_bound_starts_its_level(self) -> None:
        cases = {0.95: "very_high", 0.9499: "high", 0.85: "high", 0.8499: "medium", 0.7: "medium", 0.5: "low"}
        for score, level in {**cases, 0.4999: "very_low", float("nan"): "very_low"}.items():
            with self.subTest(score=score):
                self.assertEqual(confidence_level(score), level)


class AnsweredWriteDetailTests(SimpleTestCase):
    """The record explains only a value the cell shows."""

    JUDGED = AgentCellDetail(confidence=0.9, reason="why", unconfirmed="")

    def _resolve(self, verdict: RowVerdict, cause: StoredCellState = StoredCellState.NO_EVIDENCE):
        return AnsweredWrite("answer", "Acme", cause, judgement=self.JUDGED).resolve(verdict)

    def test_a_landed_answer_keeps_its_judgement(self) -> None:
        state = self._resolve(RowVerdict(("answer",), (), ()))
        self.assertEqual((state.state, state.detail), (StoredCellState.FILLED, self.JUDGED))

    def test_an_occupied_cell_says_nothing_about_the_run(self) -> None:
        state = self._resolve(RowVerdict((), ("answer",), ()))
        # FAILS if the reason explains a value the cell does not show.
        self.assertEqual((state.state, state.detail), (StoredCellState.FILLED, None))

    def test_a_refused_answer_says_nothing(self) -> None:
        state = self._resolve(RowVerdict((), (), (CellMismatch("answer", "not a number"),)))
        self.assertEqual((state.state, state.detail), (StoredCellState.TYPE_MISMATCH, None))

    def test_only_unverified_keeps_a_blank_s_judgement(self) -> None:
        empty = RowVerdict((), (), ())
        self.assertEqual(self._resolve(empty, StoredCellState.UNVERIFIED).detail, self.JUDGED)
        self.assertIsNone(self._resolve(empty, StoredCellState.NO_EVIDENCE).detail)

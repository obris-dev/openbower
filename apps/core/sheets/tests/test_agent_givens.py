"""A run on a row that already holds some of its agent's outputs asks
only for the rest, told the values it cannot replace as facts, so its
answers agree with them; the held columns are recorded as filled."""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TransactionTestCase
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.runtime.prompts import render_givens
from common.testing import TEST_IDENTITY
from jobs.services import JobRunner
from sheets.rows import rows_without_external_ids

from ..constants import CellSource, StoredCellState
from ..models import NodeRun, SheetCellState, SheetRow
from ..services.sheets import SheetService
from .fill_helpers import start_fill, type_cells
from .test_fill_worker import WORKER, model_patches
from .test_unconfirmed_answers import outputs_config

ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]


class _Recorder:
    """A model that answers every field it is asked for, recording the
    prompt and the answer fields of each call."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.prompts: list[str] = []
        self.fields: list[set[str]] = []

    def model(self) -> FunctionModel:
        def fn(messages, info: AgentInfo):
            self.prompts.append(messages[0].parts[-1].content)
            names = set(info.output_tools[0].parameters_json_schema["properties"])
            self.fields.append(names)
            args: dict = {}
            for key, value in self.answers.items():
                if key in names:
                    args.update({key: value, f"{key}_bwr_confidence": 0.95, f"{key}_bwr_confidence_reason": "why"})
            return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])

        return FunctionModel(fn)


class AgentGivensTests(TransactionTestCase):
    def _run(self, typed: dict[str, str], answers: dict[str, str], *, unrecorded: dict | None = None) -> _Recorder:
        self.sheets = SheetService(account_id=ACCOUNT)
        self.sheet = self.sheets.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.sheets.add_rows(self.sheet, rows_without_external_ids([{"company": "acme.com"}]))
        with patch("sheets.services.runnable.model_for"):
            _, fill = start_fill(
                str(self.sheet.id), account_id=ACCOUNT, user_id=USER, config=outputs_config("founder", "linkedin")
            )
        self.sheet.refresh_from_db()
        self.row = self.sheets.rows_page(self.sheet, limit=1)[0]
        if typed:
            type_cells(self.sheet, str(self.row.id), typed)
        if unrecorded:
            # A value on the row with no cell record behind it.
            SheetRow.objects.filter(id=self.row.id).update(data={**self.row.data, **unrecorded})
        JobRunner(worker_id="test:1").tick()
        from ..operations.consume_node_runs import handle_node_run

        recorder = _Recorder(answers)
        with model_patches(recorder.model()):
            for task_id in NodeRun.objects.filter(fill_run_id=str(fill.id)).values_list("id", flat=True):
                handle_node_run(str(task_id), WORKER)
        self.data = SheetRow.objects.get(id=self.row.id).data
        return recorder

    def _record(self, key: str) -> SheetCellState:
        return SheetCellState.objects.get(row_id=str(self.row.id), column_key=key)

    def test_a_held_output_is_told_not_asked(self) -> None:
        recorder = self._run({"founder": "Jane Doe"}, {"founder": "Someone Else", "linkedin": "Jane's profile"})
        [fields] = recorder.fields
        # FAILS if the model is asked again for an answer it cannot replace.
        self.assertNotIn("founder", fields)
        self.assertIn("linkedin", fields)
        # FAILS if the held value is not stated for the answer to agree with.
        self.assertIn("- Founder: Jane Doe", recorder.prompts[0])
        self.assertEqual((self.data["founder"], self.data["linkedin"]), ("Jane Doe", "Jane's profile"))

    def test_the_held_column_keeps_its_record_and_the_row_is_done(self) -> None:
        self._run({"founder": "Jane Doe"}, {"linkedin": "Jane's profile"})
        founder = self._record("founder")
        self.assertEqual((founder.state, founder.source), (StoredCellState.FILLED, CellSource.MANUAL))
        self.assertIsNone(founder.detail)
        self.assertEqual(self._record("linkedin").state, StoredCellState.FILLED)

    def test_a_held_value_with_no_record_is_recorded_filled(self) -> None:
        self._run({}, {"linkedin": "Jane's profile"}, unrecorded={"founder": "Jane Doe"})
        # FAILS if a given is left unrecorded: every later fill would
        # find the row owed and ask it again.
        self.assertEqual(self._record("founder").state, StoredCellState.FILLED)
        self.assertEqual(self.data["founder"], "Jane Doe")

    def test_a_row_with_nothing_held_is_asked_for_everything(self) -> None:
        recorder = self._run({}, {"founder": "Jane Doe", "linkedin": "Jane's profile"})
        self.assertTrue({"founder", "linkedin"} <= recorder.fields[0])
        self.assertNotIn("Already known", recorder.prompts[0])


class RenderGivensTests(SimpleTestCase):
    def test_givens_read_as_labelled_facts(self) -> None:
        text = render_givens({"Founder": "Jane Doe", "Domain": "acme.com"})
        self.assertIn("treat as true", text)
        self.assertIn("- Founder: Jane Doe\n- Domain: acme.com", text)
        self.assertEqual(render_givens({}), "")

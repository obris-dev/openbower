"""The fill-task state machine's processing half: handle_fill_task claims
a task (READY | QUEUED -> PROCESSING), resolves the agent live, runs it
through the mocked model seam, and lands the cell with NO fill run. A
duplicate delivery loses the claim CAS and is dropped; a gone row or list
settles terminally; a retriable blank parks; a run at the attempt cap
gives up with a diagnosed blank. The model is a scripted FunctionModel
through the same patched seam the fill worker tests use; everything else
(lists, the state machine, landing) is real.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_consume_fill_tasks
"""

from __future__ import annotations

from openbower_schema.fills import CellRunResult

from ..constants import FILL_ROW_ATTEMPTS, FillTaskStatus, StoredCellState
from ..models import FillCellState, FillTask, List, ListRow
from ..operations.consume_fill_tasks import handle_fill_task
from ..services.fill_processing import ProcessFillTask
from .test_fill_tasks import AutofillHarness
from .test_fill_worker import _patches, answering_model, throttling_model

WORKER = "test:consumer"


class ProcessFillTaskTests(AutofillHarness):
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
            return handle_fill_task(str(task.id), WORKER)

    def test_happy_path_claims_writes_the_cell_and_settles_done(self) -> None:
        _, task, row_id = self._one_ready_task()
        self.assertEqual(task.status, FillTaskStatus.READY)

        result = self._handle(task, answering_model(lambda prompt: "found it"))
        self.assertEqual(result, "done")

        # 1) The value lands on the sheet row.
        row = ListRow.objects.get(id=row_id)
        self.assertEqual(row.data["answer"], "found it")
        # 2) A FillCellState, FILLED, with NO fill run (the automatic path).
        cell = FillCellState.objects.get(row_id=row_id, column_key="answer")
        self.assertEqual(cell.state, StoredCellState.FILLED)
        self.assertIsNone(cell.fill_run_id)
        # 3) The task walked to PROCESSING (attempt stamped) and settled DONE.
        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.DONE)
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

        self.assertEqual(handle_fill_task(str(task.id), WORKER), "row_missing")

        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.ROW_MISSING)
        self.assertFalse(FillCellState.objects.filter(row_id=row_id).exists())

    def test_a_vanished_list_settles_list_missing(self) -> None:
        sheet, task, row_id = self._one_ready_task("orphan.co")
        # Delete ONLY the List row (no cascade): the ListRow survives, so
        # the processor resolves the row but not its list.
        List.objects.filter(id=sheet.id).delete()

        self.assertEqual(handle_fill_task(str(task.id), WORKER), "list_missing")

        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.LIST_MISSING)
        self.assertFalse(FillCellState.objects.filter(row_id=row_id).exists())

    def test_a_transient_blank_parks_back_to_ready_with_a_backoff(self) -> None:
        _, task, row_id = self._one_ready_task("throttled.co")

        result = self._handle(task, throttling_model())
        self.assertEqual(result, "parked")

        task.refresh_from_db()
        # Parked: back to READY, a real backoff in not_before, nothing
        # diagnosed on the sheet (the cell still shimmers).
        self.assertEqual(task.status, FillTaskStatus.READY)
        self.assertIsNotNone(task.not_before)
        self.assertTrue(task.parked)
        self.assertEqual(task.leased_by, "")
        self.assertFalse(FillCellState.objects.filter(row_id=row_id).exists())
        # The run IS stored (its refusals are the audit the give-up reads).
        self.assertEqual(task.result["declined_cause"], StoredCellState.TRANSIENT)

    def test_at_the_attempt_cap_it_gives_up_with_a_diagnosed_blank(self) -> None:
        _, task, row_id = self._one_ready_task("exhausted.co")
        # One shy of the cap: the claim stamps the attempt that tips it
        # over (attempts > FILL_ROW_ATTEMPTS), so this claim gives up
        # rather than running the model.
        FillTask.objects.filter(id=task.id).update(attempts=FILL_ROW_ATTEMPTS)

        # The model would answer if reached; give-up must short-circuit it.
        result = self._handle(task, answering_model(lambda prompt: "never reached"))
        self.assertEqual(result, "done")

        row = ListRow.objects.get(id=row_id)
        self.assertNotIn("answer", row.data)  # blank landed, not the model's answer
        cell = FillCellState.objects.get(row_id=row_id, column_key="answer")
        self.assertEqual(cell.state, StoredCellState.NO_EVIDENCE)
        self.assertIsNone(cell.fill_run_id)
        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.DONE)
        self.assertEqual(task.attempts, FILL_ROW_ATTEMPTS + 1)

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
        task = FillTask(result=prior.model_dump())
        blank = ProcessFillTask(task=task, worker_id=WORKER)._give_up_blank()
        self.assertEqual(blank.blamed_tool, "web_search")
        self.assertEqual(blank.declined_cause, StoredCellState.TRANSIENT)
        self.assertEqual(blank.tools, {"web_search": "rate_limited"})

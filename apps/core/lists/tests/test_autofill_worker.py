"""Autofill end to end: a pushed row enqueues null-run FillTasks on the
shared spine (via handle_ingest_event, inside the apply transaction),
and the autofill worker resolves each agent LIVE, runs it through the
mocked model seam, and lands the cell with no fill run. The model is a
scripted FunctionModel through the same patched model_for seam the fill
worker tests use; everything else (lists, the queue, landing) is real.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_autofill_worker
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from unittest.mock import patch

from django.test import TransactionTestCase

from ..constants import FillTaskStatus, StoredCellState
from ..ingest.consumer import handle_ingest_event
from ..ingest.events import IngestEvent
from ..models import FillCellState, FillTask, List, ListRow
from ..operations.autofill_worker import AutofillWorkerOperation
from ..services import autofill
from ..services.fill_admission import FillAdmissionService
from ..services.fill_queue import FillQueueService
from ..services.lists import ListService
from .test_fill_worker import _patches, answering_model, quick_config

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"


class AutofillTestCase(TransactionTestCase):
    """Single-threaded worker, but the same harness as the fill worker
    tests: TransactionTestCase so a worker that opens its own connection
    (and the admission's select_for_update) never deadlocks against a
    test-wrapping transaction."""

    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT, user_id=USER)

    def _ai_sheet(self, *, rows: int = 2):
        """A sheet with one AI column bound to an agent, built the real
        way: ListService.create + admission (which mints the ephemeral
        agent and stamps the column's `fill.agent_id`). Returns the
        sheet and the agent id the autofill worker will resolve."""
        sheet = self.lists.create(
            label="Prospects", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(sheet, [{"company": f"seed{n}.com"} for n in range(rows)])
        with patch("lists.services.fill_admission.base.model_for"):
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(sheet.id), config=quick_config(), confirmed_row_count=rows
            )
        sheet.refresh_from_db()
        return sheet, fill.agent_id, fill

    def _plain_sheet(self):
        sheet = self.lists.create(
            label="Plain", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        return sheet

    def _push(self, sheet, rows: list[dict], *, account_id: str = ACCOUNT, list_id: str | None = None) -> str:
        event = IngestEvent(
            event_id=f"evt-{ListRow.objects.count()}-{id(rows)}",
            list_id=list_id or str(sheet.id),
            account_id=account_id,
            user_id=USER,
            rows=rows,
            received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        )
        return handle_ingest_event(event)

    def _null_run_tasks(self):
        return FillTask.objects.filter(fill_run_id__isnull=True)

    def _new_row_ids(self, sheet, before: set[str]) -> set[str]:
        return {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))} - before

    def run_worker(self, model) -> None:
        op = AutofillWorkerOperation(worker_id="test:autofill", stop=threading.Event())
        with _patches(model):
            op.run(once=True)


class EnqueueTests(AutofillTestCase):
    def test_a_push_to_an_ai_sheet_enqueues_one_null_run_task_per_row_and_agent(self) -> None:
        sheet, agent_id, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}

        self.assertEqual(self._push(sheet, [{"company": "newco.com"}, {"company": "newco2.io"}]), "applied")

        new_ids = self._new_row_ids(sheet, before)
        tasks = list(self._null_run_tasks())
        # One task per (pushed row, distinct agent among the AI columns):
        # two rows, one agent => exactly two null-run tasks.
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t.row_id for t in tasks}, new_ids)
        for task in tasks:
            self.assertIsNone(task.fill_run_id)
            self.assertEqual(task.agent_id, agent_id)
            self.assertEqual(task.account_id, ACCOUNT)
            self.assertEqual(task.status, FillTaskStatus.QUEUED)

    def test_a_push_to_a_sheet_with_no_ai_columns_enqueues_nothing(self) -> None:
        plain = self._plain_sheet()
        self.assertEqual(self._push(plain, [{"company": "newco.com"}]), "applied")
        self.assertEqual(self._null_run_tasks().count(), 0)

    def test_enqueue_rides_the_apply_transaction(self) -> None:
        sheet, _, _ = self._ai_sheet()
        rows_before = ListRow.objects.filter(list_id=str(sheet.id)).count()
        tasks_before = self._null_run_tasks().count()

        # An applied event: rows and null-run tasks move together.
        self.assertEqual(
            self._push(sheet, [{"company": "a.com"}, {"company": "b.io"}, {"company": "c.net"}]), "applied"
        )
        rows_added = ListRow.objects.filter(list_id=str(sheet.id)).count() - rows_before
        tasks_added = self._null_run_tasks().count() - tasks_before
        self.assertEqual(rows_added, 3)
        self.assertEqual(tasks_added, 3)

        # A dropped event (the list is gone) enqueues nothing and adds no rows.
        self.assertEqual(self._push(sheet, [{"company": "x.com"}], list_id="01JQ" + "Z" * 22), "dropped")
        self.assertEqual(self._null_run_tasks().count() - tasks_before, 3)

    def test_re_enqueue_of_the_same_row_and_agent_is_a_no_op(self) -> None:
        sheet, _, _ = self._ai_sheet()
        created = self.lists.add_rows(sheet, [{"company": "dupe.com"}])
        sheet.refresh_from_db()

        first = autofill.enqueue_rows(account_id=ACCOUNT, target=sheet, rows=created)
        self.assertEqual(first, 1)
        self.assertEqual(self._null_run_tasks().filter(row_id=str(created[0].id)).count(), 1)

        # The partial unique (row_id, agent_id) WHERE fill_run_id IS NULL
        # makes a second enqueue a no-op (bulk_create ignore_conflicts).
        autofill.enqueue_rows(account_id=ACCOUNT, target=sheet, rows=created)
        self.assertEqual(self._null_run_tasks().filter(row_id=str(created[0].id)).count(), 1)


class WorkerTests(AutofillTestCase):
    def test_happy_path_writes_the_cell_the_cell_state_and_settles_done(self) -> None:
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "target.co"}])
        [row_id] = self._new_row_ids(sheet, before)

        self.run_worker(answering_model(lambda prompt: "found it"))

        # 1) The value lands on the sheet row.
        row = ListRow.objects.get(id=row_id)
        self.assertEqual(row.data["answer"], "found it")
        # 2) A FillCellState, FILLED, with NO fill run (the automatic path).
        cell = FillCellState.objects.get(row_id=row_id, column_key="answer")
        self.assertEqual(cell.state, StoredCellState.FILLED)
        self.assertIsNone(cell.fill_run_id)
        # 3) The task settles DONE.
        task = self._null_run_tasks().get(row_id=row_id)
        self.assertEqual(task.status, FillTaskStatus.DONE)

    def test_a_vanished_list_settles_list_missing_and_writes_nothing(self) -> None:
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "orphan.co"}])
        [row_id] = self._new_row_ids(sheet, before)

        # Delete ONLY the List row (no cascade): the ListRow survives, so
        # the worker resolves the row but not its list.
        List.objects.filter(id=sheet.id).delete()

        self.run_worker(answering_model(lambda prompt: "unused"))

        task = self._null_run_tasks().get(row_id=row_id)
        self.assertEqual(task.status, FillTaskStatus.LIST_MISSING)
        self.assertFalse(FillCellState.objects.filter(row_id=row_id).exists())

    def test_a_vanished_row_settles_row_missing(self) -> None:
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "gone.co"}])
        [row_id] = self._new_row_ids(sheet, before)

        ListRow.objects.filter(id=row_id).delete()

        self.run_worker(answering_model(lambda prompt: "unused"))

        task = self._null_run_tasks().get(row_id=row_id)
        self.assertEqual(task.status, FillTaskStatus.ROW_MISSING)
        self.assertFalse(FillCellState.objects.filter(row_id=row_id).exists())

    def test_the_two_claims_never_cross(self) -> None:
        # A fill-backed task (from admission) and a null-run task (from a
        # push) sit on the one spine. Each claim takes only its own kind.
        sheet, _, fill = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "auto.co"}])
        [auto_row_id] = self._new_row_ids(sheet, before)

        fill_queue = FillQueueService(worker_id="test:fill")
        autofill_queue = FillQueueService(worker_id="test:auto")

        # The FILL worker's claim takes the consented (fill-backed) tasks
        # and never the null-run one.
        claimed_fill = fill_queue.claim_batch(fill, free_slots=10).tasks
        self.assertTrue(claimed_fill)
        self.assertTrue(all(t.fill_run_id == str(fill.id) for t in claimed_fill))
        self.assertNotIn(auto_row_id, {t.row_id for t in claimed_fill})

        # The autofill claim takes the null-run task and never a
        # fill-backed one.
        claimed_auto = autofill_queue.claim_autofill_batch(free_slots=10)
        self.assertTrue(claimed_auto)
        self.assertTrue(all(t.fill_run_id is None for t in claimed_auto))
        self.assertEqual({t.row_id for t in claimed_auto}, {auto_row_id})

        # Disjoint by task id, both directions.
        self.assertFalse({t.id for t in claimed_fill} & {t.id for t in claimed_auto})

    def test_write_if_blank_keeps_a_user_value(self) -> None:
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "occupied.co"}])
        [row_id] = self._new_row_ids(sheet, before)
        # A value the user already put in the AI column.
        self.lists.write_cells(str(sheet.id), row_id, {"answer": "mine, by hand"})

        self.run_worker(answering_model(lambda prompt: "what the model found"))

        # The user's value stands, and the run settles rather than stalls.
        row = ListRow.objects.get(id=row_id)
        self.assertEqual(row.data["answer"], "mine, by hand")
        task = self._null_run_tasks().get(row_id=row_id)
        self.assertEqual(task.status, FillTaskStatus.DONE)
        # The model's answer is recoverable on the stored run, not lost.
        self.assertEqual(task.result["cells"]["answer"], "what the model found")

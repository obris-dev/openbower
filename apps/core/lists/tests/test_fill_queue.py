"""The fill lifecycle on the shared state machine: terminal writes
through the landing, completion (no non-terminal task remains),
cancel/fail (the ONE terminal transition), the stale reclaim, and the
account-scoped run controls. The lease-based queue is gone; task
transitions are FillTaskFlow's, and the counters DERIVE at read time.
Real DB, no mocks (all pure ORM)."""

from __future__ import annotations

import datetime
from functools import partial

from django.db import models
from django.test import TestCase
from django.utils import timezone

from openbower_schema.fills import CellRunResult

from ..constants import (
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask
from ..services import fill_progress
from ..services.fill_processing.landing import LandingContext, land_row
from ..services.fill_tasks import PROCESSING_STALE_SECONDS, FillTaskFlow
from ..services.fills import FillNotFound, FillService, derive_counters
from ..services.lists import CellWriteResult

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"


def make_run(*, status: str = FillStatus.PENDING, rows: int = 3) -> Fill:
    fill = Fill.objects.create(
        account_id=ACCOUNT,
        user_id=USER,
        list_id="01LISTAAAAAAAAAAAAAAAAAAAA",
        agent_id="01AGENTAAAAAAAAAAAAAAAAAAA",
        status=status,
        column_keys=["answer"],
        config_snapshot={},
        confirmed_row_count=rows,
    )
    # The queue is materialized at admission, born READY, so a fill under
    # test has its whole consented set of tasks from the start.
    now = timezone.now()
    for n in range(rows):
        FillTask.objects.create(
            account_id=ACCOUNT,
            fill_run_id=str(fill.id),
            row_id=f"01ROW{n:021d}",
            position=n + 1,
            status=FillTaskStatus.READY,
            last_state_change_at=now,
        )
    return fill


class _SheetThatTakesEverything:
    """The sheet writer as these tests need it: the fill here names no
    real list, so a FILLED landing is simulated by a writer that reports
    every key written. The landing's real writer is covered by the
    worker and view tests."""

    @staticmethod
    def write_cells(list_id: str, row_id: str, cells: dict[str, str]) -> CellWriteResult:
        return CellWriteResult(tuple(cells), (), ())


def land(fill: Fill, task: FillTask, *, worker: str = "test:1", state=None) -> bool:
    """Claim the task and land a run on its row the way the shared
    consumer does (FillTaskFlow claim -> land_row -> settle). A FILLED
    state means a value was written."""
    flow = FillTaskFlow(worker_id=worker)
    claimed = flow.claim(str(task.id))
    assert claimed is not None, "claim missed"
    if state is None or state == StoredCellState.FILLED:
        run = CellRunResult(cells={"answer": "x"})
    else:
        run = CellRunResult(declined_cause=state)
    return (
        land_row(
            LandingContext.from_fill(fill),
            claimed.row_id,
            run,
            close=partial(flow.settle, claimed.id, status=FillTaskStatus.DONE),
            lists=_SheetThatTakesEverything(),
        )
        is not None
    )


class TerminalWriteTests(TestCase):
    def test_terminal_write_lands_the_diagnosis(self) -> None:
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        self.assertTrue(land(fill, task, state=StoredCellState.NO_EVIDENCE))
        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.DONE)
        cell = FillCellState.objects.get()
        self.assertEqual(
            (cell.list_id, cell.row_id, cell.column_key, cell.state, cell.fill_run_id),
            (fill.list_id, task.row_id, "answer", StoredCellState.NO_EVIDENCE, str(fill.id)),
        )

    def test_a_reclaimed_tasks_original_claimant_misses_and_lands_nothing(self) -> None:
        # A claim that went stale is reclaimed to READY; the reclaiming
        # worker owns the next write, and the original claimant's
        # settle CAS (on its own stamp) misses, rolling the diagnosis
        # back with it.
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        original = FillTaskFlow(worker_id="test:1")
        claimed = original.claim(str(task.id))
        FillTask.objects.filter(id=task.id).update(
            last_state_change_at=timezone.now() - datetime.timedelta(seconds=PROCESSING_STALE_SECONDS + 60)
        )
        self.assertEqual(FillTaskFlow.reclaim_stale_processing(), 1)
        # The original claimant's terminal write now misses.
        self.assertFalse(
            land_row(
                LandingContext.from_fill(fill),
                claimed.row_id,
                CellRunResult(declined_cause=StoredCellState.NO_EVIDENCE),
                close=partial(original.settle, claimed.id, status=FillTaskStatus.DONE),
                lists=_SheetThatTakesEverything(),
            )
            is not None
        )
        self.assertFalse(FillCellState.objects.exists())

    def test_an_answered_column_overwrites_its_earlier_blank(self) -> None:
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        land(fill, task, state=StoredCellState.NO_EVIDENCE)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.NO_EVIDENCE)
        later = make_run(rows=1)
        FillTask.objects.filter(fill_run_id=str(later.id)).update(row_id=task.row_id)
        second = FillTask.objects.get(fill_run_id=str(later.id))
        self.assertTrue(land(later, second))
        self.assertEqual(FillCellState.objects.count(), 1)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.FILLED)


class CompletionTests(TestCase):
    def _drain(self, fill: Fill) -> None:
        for task in list(FillTask.objects.filter(fill_run_id=str(fill.id), status=FillTaskStatus.READY)):
            land(fill, task)

    def test_try_finish_refuses_while_work_remains(self) -> None:
        fill = make_run(rows=2)
        task = FillTask.objects.filter(fill_run_id=str(fill.id)).order_by("position").first()
        land(fill, task)
        self.assertFalse(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.PENDING)

    def test_try_finish_completes_a_drained_fill(self) -> None:
        fill = make_run(rows=2)
        self._drain(fill)
        self.assertTrue(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)

    def test_a_drained_live_fill_is_still_offered_for_completion(self) -> None:
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        land(fill, task)
        self.assertEqual([j.id for j in fill_progress.iter_live_fills()], [fill.id])
        self.assertTrue(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        self.assertEqual(list(fill_progress.iter_live_fills()), [])

    def test_a_processing_task_blocks_completion(self) -> None:
        # A task a consumer owns (PROCESSING) is still owed, so a crashed
        # claimant never fakes completion.
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        FillTaskFlow(worker_id="test:1").claim(str(task.id))
        self.assertFalse(fill_progress.try_finish(str(fill.id)))

    def test_a_parked_task_blocks_completion(self) -> None:
        fill = make_run(rows=1)
        task = FillTask.objects.get(fill_run_id=str(fill.id))
        flow = FillTaskFlow(worker_id="test:1")
        flow.claim(str(task.id))
        flow.park(str(task.id), backoff_seconds=60, result={})
        self.assertFalse(fill_progress.try_finish(str(fill.id)))

    def test_fail_fill_is_cas_from_live_states(self) -> None:
        fill = make_run(rows=1)
        self.assertTrue(fill_progress.fail(str(fill.id), code="provider_throttled", message="why"))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.FAILED)
        self.assertEqual(fill.error_code, "provider_throttled")
        self.assertFalse(fill_progress.fail(str(fill.id), code="x", message="y"))

    def test_stopping_a_fill_abandons_its_queue_and_touches_no_cell(self) -> None:
        # The record of consent granted and NOT spent. Nothing on the
        # sheet is written or unwritten: those cells were pending only
        # because a non-terminal task said so. The PROCESSING task the
        # worker still owns is left to its own terminal CAS.
        fill = make_run(rows=3)
        first = FillTask.objects.filter(fill_run_id=str(fill.id)).order_by("position").first()
        land(fill, first)
        self.assertTrue(fill_progress.cancel(str(fill.id)))
        by_status = dict(
            FillTask.objects.filter(fill_run_id=str(fill.id)).values_list("status").annotate(n=models.Count("id"))
        )
        self.assertEqual(by_status, {FillTaskStatus.DONE: 1, FillTaskStatus.ABANDONED: 2})
        self.assertEqual(FillCellState.objects.count(), 1)

    def test_counters_derive_from_tasks_and_cells(self) -> None:
        fill = make_run(rows=2)
        tasks = list(FillTask.objects.filter(fill_run_id=str(fill.id)).order_by("position"))
        land(fill, tasks[0])  # FILLED
        land(fill, tasks[1], state=StoredCellState.NO_EVIDENCE)  # blank
        counters = derive_counters(fill)
        self.assertEqual(
            (counters.attempted, counters.filled, counters.blank, counters.transient),
            (2, 1, 1, 0),
        )


class RunControlTests(TestCase):
    def test_cancel_flips_live_run_and_noops_terminal(self) -> None:
        fill = make_run(rows=1)
        service = FillService(account_id=ACCOUNT)
        cancelled = service.cancel(str(fill.id))
        self.assertEqual(cancelled.status, FillStatus.CANCELLED)
        self.assertEqual(service.cancel(str(fill.id)).status, FillStatus.CANCELLED)

    def test_a_cancelled_run_leaves_no_live_fill(self) -> None:
        fill = make_run(rows=1)
        self.assertEqual([f.id for f in fill_progress.iter_live_fills()], [fill.id])
        FillService(account_id=ACCOUNT).cancel(str(fill.id))
        self.assertEqual(list(fill_progress.iter_live_fills()), [])

    def test_foreign_account_reads_as_not_found(self) -> None:
        fill = make_run(rows=1)
        foreign = FillService(account_id="01FOREIGNAAAAAAAAAAAAAAAAA")
        with self.assertRaises(FillNotFound):
            foreign.get(str(fill.id))
        with self.assertRaises(FillNotFound):
            foreign.cancel(str(fill.id))

    def test_page_for_list_keysets_live_runs_only(self) -> None:
        fills = [make_run(rows=1) for _ in range(3)]
        cancelled = fills[0]
        FillService(account_id=ACCOUNT).cancel(str(cancelled.id))
        live_newest_first = sorted((str(fill.id) for fill in fills[1:]), reverse=True)
        service = FillService(account_id=ACCOUNT)
        page = service.page_for_list("01LISTAAAAAAAAAAAAAAAAAAAA", after_id="", limit=1)
        self.assertEqual([str(fill.id) for fill in page], live_newest_first[:1])
        rest = service.page_for_list("01LISTAAAAAAAAAAAAAAAAAAAA", after_id=str(page[-1].id), limit=2)
        self.assertEqual([str(fill.id) for fill in rest], live_newest_first[1:])
        self.assertNotIn(str(cancelled.id), [str(fill.id) for fill in (*page, *rest)])

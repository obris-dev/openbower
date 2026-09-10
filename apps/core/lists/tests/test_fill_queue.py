"""The fill queue's state machine: claim order, lease CAS, completion
CAS, transient patience, cancel-flips-first. Real DB, no mocks (the
queue is pure ORM)."""

from __future__ import annotations

import datetime
from functools import partial

from django.db import models
from django.test import TestCase
from django.utils import timezone

from openbower_schema.fills import CellRunResult

from ..constants import (
    FILL_CLAIM_BATCH,
    FILL_ROW_ATTEMPTS,
    ROW_LEASE_STALE_SECONDS,
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask
from ..services import fill_progress
from ..services.fill_queue import FillQueueService
from ..services.fills import FillNotFound, FillService
from ..services.landing import LandingContext, land_row
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
    # The queue is materialized at admission, so a fill under test has
    # its whole consented set of tasks from the start.
    for n in range(rows):
        FillTask.objects.create(account_id=ACCOUNT, fill_run_id=str(fill.id), row_id=f"01ROW{n:021d}", position=n + 1)
    return fill


class ClaimTests(TestCase):
    def setUp(self) -> None:
        self.queue = FillQueueService(worker_id="test:1")

    def test_claim_takes_queued_tasks_and_flips_the_fill_running(self) -> None:
        fill = make_run(rows=3)
        batch = self.queue.claim_batch(fill, free_slots=2)
        self.assertEqual(len(batch.tasks), 2)
        for task in batch.tasks:
            self.assertEqual(task.leased_by, "test:1")
            self.assertIsNotNone(task.leased_at)
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.RUNNING)

    def test_claim_counts_the_attempt(self) -> None:
        # At CLAIM, not at completion, so a task that kills its worker
        # thread still walks toward the cap across process restarts.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.assertEqual(task.attempts, 1)
        task.refresh_from_db()
        self.assertEqual(task.attempts, 1)

    def test_claim_is_bounded_by_free_slots_and_batch(self) -> None:
        fill = make_run(rows=FILL_CLAIM_BATCH + 8)
        self.assertEqual(len(self.queue.claim_batch(fill, free_slots=100).tasks), FILL_CLAIM_BATCH)
        self.assertEqual(len(self.queue.claim_batch(fill, free_slots=0).tasks), 0)

    def test_claim_walks_the_sheet_in_position_order(self) -> None:
        fill = make_run(rows=3)
        batch = self.queue.claim_batch(fill, free_slots=3)
        self.assertEqual([task.position for task in batch.tasks], [1, 2, 3])

    def test_fresh_lease_is_not_reclaimable(self) -> None:
        fill = make_run(rows=1)
        self.queue.claim_batch(fill, free_slots=1)
        other = FillQueueService(worker_id="test:2")
        self.assertEqual(len(other.claim_batch(fill, free_slots=1).tasks), 0)

    def test_stale_lease_reclaims(self) -> None:
        fill = make_run(rows=1)
        batch = self.queue.claim_batch(fill, free_slots=1)
        stale = timezone.now() - datetime.timedelta(seconds=ROW_LEASE_STALE_SECONDS + 1)
        FillTask.objects.filter(id=batch.tasks[0].id).update(leased_at=stale)
        other = FillQueueService(worker_id="test:2")
        reclaimed = other.claim_batch(fill, free_slots=1)
        self.assertEqual(len(reclaimed.tasks), 1)
        self.assertEqual(reclaimed.tasks[0].leased_by, "test:2")

    def test_a_parked_task_waits_out_its_backoff_then_returns(self) -> None:
        # A park backs off in TIME. Under the old shape a parked row
        # waited out a lease it no longer held, which meant the retry
        # window and the death-detection window were the same number.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.assertTrue(self.queue.park_task(task, backoff_seconds=60, result={}))
        self.assertEqual(len(self.queue.claim_batch(fill, free_slots=1).tasks), 0)
        FillTask.objects.filter(id=task.id).update(not_before=timezone.now() - datetime.timedelta(seconds=1))
        again = self.queue.claim_batch(fill, free_slots=1).tasks
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0].attempts, 2)

    def test_a_park_diagnoses_nothing(self) -> None:
        # Nothing terminal happened: the cell is still owed and still
        # shimmers, because its task is still queued.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.queue.park_task(task, backoff_seconds=0, result={})
        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.QUEUED)
        self.assertFalse(FillCellState.objects.exists())

    def test_renew_leases_bumps_only_own_live_leases(self) -> None:
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        old = timezone.now() - datetime.timedelta(seconds=60)
        FillTask.objects.filter(id=task.id).update(leased_at=old)
        self.queue.renew_leases([task])
        task.refresh_from_db()
        self.assertGreater(task.leased_at, old)
        FillTask.objects.filter(id=task.id).update(leased_at=old)
        # A reclaimed task's ORIGINAL claimant cannot resurrect its
        # lease from the bulk renewal either.
        FillQueueService(worker_id="test:other").renew_leases([task])
        task.refresh_from_db()
        self.assertEqual(task.leased_at, old)


class _SheetThatTakesEverything:
    """The sheet writer as these QUEUE tests need it: the fill here
    names no real list (the fixture is the queue alone), so a FILLED
    landing is simulated by a writer that reports every key written.
    The landing's real writer is covered by the worker and view tests."""

    @staticmethod
    def write_cells(list_id: str, row_id: str, cells: dict[str, str]) -> CellWriteResult:
        return CellWriteResult(tuple(cells), (), ())


def land(queue, fill, task, *, state=None):
    """Land a run on the task's row through the landing, the way every
    terminal writer does: a FILLED state means a value was written."""
    if state is None or state == StoredCellState.FILLED:
        run = CellRunResult(cells={"answer": "x"})
    else:
        run = CellRunResult(declined_cause=state)
    return (
        land_row(
            LandingContext.from_fill(fill),
            task.row_id,
            run,
            close=partial(queue.complete_task, task),
            lists=_SheetThatTakesEverything(),
        )
        is not None
    )


class TerminalWriteTests(TestCase):
    def setUp(self) -> None:
        self.queue = FillQueueService(worker_id="test:1")

    def _complete(self, queue, fill, task, *, state=None):
        return land(queue, fill, task, state=state)

    def test_complete_is_cas_on_own_lease(self) -> None:
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.assertTrue(self._complete(self.queue, fill, task))
        task.refresh_from_db()
        self.assertEqual(task.status, FillTaskStatus.DONE)
        self.assertEqual(task.leased_by, "")
        # A second write (any claimant) misses: terminal is immutable.
        self.assertFalse(self._complete(self.queue, fill, task, state=StoredCellState.NO_EVIDENCE))

    def test_reclaimed_tasks_original_claimant_misses(self) -> None:
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        stale = timezone.now() - datetime.timedelta(seconds=ROW_LEASE_STALE_SECONDS + 1)
        FillTask.objects.filter(id=task.id).update(leased_at=stale)
        other = FillQueueService(worker_id="test:2")
        other.claim_batch(fill, free_slots=1)
        self.assertFalse(self._complete(self.queue, fill, task))

    def test_terminal_writes_land_the_diagnosis_and_misses_do_not(self) -> None:
        # The diagnosis writes in the CAS's own transaction: a landed
        # write leaves one record per column the fill owns; a reclaimed
        # task's original claimant leaves NOTHING (the reclaiming worker
        # owns that cell's next write).
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        stale = timezone.now() - datetime.timedelta(seconds=ROW_LEASE_STALE_SECONDS + 1)
        FillTask.objects.filter(id=task.id).update(leased_at=stale)
        other = FillQueueService(worker_id="test:2")
        reclaimed = other.claim_batch(fill, free_slots=1).tasks[0]
        self.assertFalse(self._complete(self.queue, fill, task, state=StoredCellState.NO_EVIDENCE))
        self.assertFalse(FillCellState.objects.exists())
        self.assertTrue(self._complete(other, fill, reclaimed, state=StoredCellState.NO_EVIDENCE))
        cell = FillCellState.objects.get()
        self.assertEqual(
            (cell.list_id, cell.row_id, cell.column_key, cell.state, cell.fill_run_id),
            (fill.list_id, task.row_id, "answer", StoredCellState.NO_EVIDENCE, str(fill.id)),
        )

    def test_an_answered_column_overwrites_its_earlier_blank(self) -> None:
        # One record per cell, upserted: a later fill that answers it
        # flips the SAME row to FILLED rather than adding a second, so
        # filled-plus-blank stays the count of cells resolved.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self._complete(self.queue, fill, task, state=StoredCellState.NO_EVIDENCE)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.NO_EVIDENCE)
        later = make_run(rows=1)
        FillTask.objects.filter(fill_run_id=str(later.id)).update(row_id=task.row_id)
        second = self.queue.claim_batch(later, free_slots=1).tasks[0]
        self.assertTrue(self._complete(self.queue, later, second))
        self.assertEqual(FillCellState.objects.count(), 1)
        self.assertEqual(FillCellState.objects.get().state, StoredCellState.FILLED)

    def test_exhaustion_is_read_off_the_claim(self) -> None:
        # Decided from the attempt the claim stamped, so a task that
        # exhausted its retries and one that died mid-run at the cap
        # resolve by the same rule, and neither can strand its fill.
        fill = make_run(rows=1)
        for _ in range(FILL_ROW_ATTEMPTS):
            task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
            self.assertFalse(self.queue.exhausted(task))
            self.queue.park_task(task, backoff_seconds=0, result={})
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.assertEqual(task.attempts, FILL_ROW_ATTEMPTS + 1)
        self.assertTrue(self.queue.exhausted(task))


class CompletionTests(TestCase):
    def setUp(self) -> None:
        self.queue = FillQueueService(worker_id="test:1")

    def _drain(self, fill: Fill) -> None:
        while True:
            batch = self.queue.claim_batch(fill, free_slots=FILL_CLAIM_BATCH)
            if not batch.tasks:
                return
            for task in batch.tasks:
                land(self.queue, fill, task)

    def test_try_finish_refuses_while_work_remains(self) -> None:
        fill = make_run(rows=2)
        batch = self.queue.claim_batch(fill, free_slots=1)
        land(self.queue, fill, batch.tasks[0])
        self.assertFalse(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.RUNNING)

    def test_try_finish_completes_a_drained_fill(self) -> None:
        fill = make_run(rows=2)
        self._drain(fill)
        self.assertTrue(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)

    def test_a_drained_live_fill_is_still_offered_for_completion(self) -> None:
        # A claimant that dies between its last terminal write and
        # try_finish leaves the fill LIVE with nothing claimable. The
        # supervisor is what flips it (nothing claimed, nothing in
        # flight -> try_finish), so live_fills must keep offering it
        # rather than filtering it out for having no claimable work.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        land(self.queue, fill, task)
        self.assertEqual([j.id for j in fill_progress.live_fills()], [fill.id])
        self.assertTrue(fill_progress.try_finish(str(fill.id)))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        self.assertEqual(fill_progress.live_fills(), [])

    def test_a_stale_leased_task_blocks_completion(self) -> None:
        # A crashed claimant never fakes completion: its task is still
        # QUEUED (stale-leased), so the fill stays live for reclaim.
        fill = make_run(rows=1)
        batch = self.queue.claim_batch(fill, free_slots=1)
        stale = timezone.now() - datetime.timedelta(seconds=ROW_LEASE_STALE_SECONDS + 1)
        FillTask.objects.filter(id=batch.tasks[0].id).update(leased_at=stale)
        self.assertFalse(fill_progress.try_finish(str(fill.id)))

    def test_a_parked_task_blocks_completion(self) -> None:
        # It is still owed, so the fill is not done. Under the old
        # shape an exhausted retry sat in a terminal-looking state that
        # every reader had to re-derive as finished.
        fill = make_run(rows=1)
        task = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        self.queue.park_task(task, backoff_seconds=60, result={})
        self.assertFalse(fill_progress.try_finish(str(fill.id)))

    def test_fail_fill_is_cas_from_live_states(self) -> None:
        fill = make_run(rows=1)
        self.assertTrue(fill_progress.fail(str(fill.id), code="provider_throttled", message="why"))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.FAILED)
        self.assertEqual(fill.error_code, "provider_throttled")
        self.assertFalse(fill_progress.fail(str(fill.id), code="x", message="y"))

    def test_stopping_a_fill_abandons_its_queue_and_touches_no_cell(self) -> None:
        # The record of consent granted and NOT spent, which is what a
        # later resume reads instead of reconstructing. And nothing on
        # the sheet is written or unwritten: those cells were pending
        # only because a QUEUED task said so.
        fill = make_run(rows=3)
        claimed = self.queue.claim_batch(fill, free_slots=1).tasks[0]
        land(self.queue, fill, claimed)
        self.assertTrue(fill_progress.cancel(str(fill.id)))
        by_status = dict(
            FillTask.objects.filter(fill_run_id=str(fill.id)).values_list("status").annotate(n=models.Count("id"))
        )
        self.assertEqual(by_status, {FillTaskStatus.DONE: 1, FillTaskStatus.ABANDONED: 2})
        # ONE cell state, from the task that actually ran. The two the
        # fill never reached have none: stopping writes nothing to the
        # sheet, because nothing was written for them at admission.
        self.assertEqual(FillCellState.objects.count(), 1)

    def test_counters_accumulate_and_stamp_heartbeat(self) -> None:
        fill = make_run(rows=2)
        fill_progress.bump(str(fill.id), attempted=1, filled=1)
        fill_progress.bump(str(fill.id), attempted=1, blank=1)
        fill.refresh_from_db()
        self.assertEqual((fill.attempted, fill.filled, fill.blank), (2, 1, 1))
        self.assertIsNotNone(fill.heartbeat_at)


class RunControlTests(TestCase):
    def test_cancel_flips_live_run_and_noops_terminal(self) -> None:
        fill = make_run(rows=1)
        service = FillService(account_id=ACCOUNT)
        cancelled = service.cancel(str(fill.id))
        self.assertEqual(cancelled.status, FillStatus.CANCELLED)
        # Terminal cancel is a no-op, not an error: the user's intent
        # (no further spend) already holds.
        self.assertEqual(service.cancel(str(fill.id)).status, FillStatus.CANCELLED)

    def test_cancelled_run_stops_the_worker_gate(self) -> None:
        fill = make_run(rows=1)
        self.assertTrue(fill_progress.is_live(str(fill.id)))
        FillService(account_id=ACCOUNT).cancel(str(fill.id))
        self.assertFalse(fill_progress.is_live(str(fill.id)))
        # And it leaves the live set, so the supervisor evicts its
        # per-fill state instead of serving a cancelled fill.
        self.assertEqual(fill_progress.live_fills(), [])

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
        # Expected order comes from the IDS, not from creation order.
        # ULIDs are time-monotonic at MILLISECOND resolution only (see
        # min_ulid_at), so fills minted inside one millisecond share a
        # time prefix and their random suffixes decide the sort.
        live_newest_first = sorted((str(fill.id) for fill in fills[1:]), reverse=True)
        service = FillService(account_id=ACCOUNT)
        page = service.page_for_list("01LISTAAAAAAAAAAAAAAAAAAAA", after_id="", limit=1)
        self.assertEqual([str(fill.id) for fill in page], live_newest_first[:1])
        rest = service.page_for_list("01LISTAAAAAAAAAAAAAAAAAAAA", after_id=str(page[-1].id), limit=2)
        self.assertEqual([str(fill.id) for fill in rest], live_newest_first[1:])
        # The cancelled run is on NO page: its story is the column
        # summary's to tell, and a poll that re-shipped every dead run
        # forever would grow without bound.
        self.assertNotIn(str(cancelled.id), [str(fill.id) for fill in (*page, *rest)])

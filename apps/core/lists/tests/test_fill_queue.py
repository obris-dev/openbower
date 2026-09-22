"""The fill lifecycle on the shared state machine: terminal writes
through the landing, completion (the fill job's poll finds no open
run), cancel/fail (the ONE terminal transition, a stop from outside),
the stale reclaim, and the account-scoped run controls. A fill is a
job; task transitions are NodeRunFlow's, and the counters DERIVE at
read time. Real DB, no mocks (all pure ORM)."""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.db import models
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from jobs.constants import JobStatus
from jobs.models import Job
from openbower_kernel.ranks import keys_between
from openbower_schema.fills import CellRunResult

from ..constants import NodeRunStatus, StoredCellState
from ..models import ListCellState, NodeRun
from ..nodes.registry import COLUMN_AGENT
from ..services import fill_progress
from ..services.fill_processing.landing import LandingContext, land_row
from ..services.fills import FillNotFound, FillService, page_progress
from ..services.lists import CellWriteResult, ListService
from ..services.node_runs import PROCESSING_STALE_SECONDS, NodeRunFlow
from .fill_helpers import fill_status, open_fill_job, tick_fill

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"
LIST = "01LISTAAAAAAAAAAAAAAAAAAAA"
NODE = "01NODEAAAAAAAAAAAAAAAAAAAA"
AGENT = "01AGENTAAAAAAAAAAAAAAAAAAA"


def make_run(*, rows: int = 3, list_id: str = LIST) -> Job:
    """A targeted fill job (its walk done) with its whole consented set
    of tasks, born READY, so a lifecycle test starts from a fill that is
    polling its runs."""
    fill = open_fill_job(
        account_id=ACCOUNT,
        user_id=USER,
        list_id=list_id,
        node_id=NODE,
        agent_id=AGENT,
        column_keys=["answer"],
        consented=rows,
    )
    now = timezone.now()
    for n, rank in enumerate(keys_between(None, None, rows)):
        NodeRun.objects.create(
            account_id=ACCOUNT,
            fill_run_id=str(fill.id),
            node_id=NODE,
            kind=COLUMN_AGENT,
            row_id=f"01ROW{n:021d}",
            list_id=list_id,
            rank=rank,
            status=NodeRunStatus.READY,
            last_state_change_at=now,
        )
    return fill


class _SheetThatTakesEverything(ListService):
    """The sheet writer as these tests need it: the fill here names no
    real list, so the VALUE half is faked to report every key written
    while the truth half stays the real one. The landing's real value
    writer is covered by the worker and view tests."""

    def _write_values(self, list_id: str, row_id: str, cells: dict[str, str]) -> CellWriteResult:
        return CellWriteResult(tuple(cells), (), ())


def _ctx(fill: Job, task: NodeRun) -> LandingContext:
    return LandingContext(
        account_id=ACCOUNT,
        list_id=fill.target_id,
        column_keys=("answer",),
        fill_run_id=str(fill.id),
    )


def land(fill: Job, task: NodeRun, *, worker: str = "test:1", state=None) -> bool:
    """Claim the task and land a run on its row the way the shared
    consumer does (NodeRunFlow claim -> land_row -> settle). A FILLED
    state means a value was written."""
    flow = NodeRunFlow(worker_id=worker)
    claimed = flow.claim(str(task.id))
    assert claimed is not None, "claim missed"
    if state is None or state == StoredCellState.FILLED:
        run = CellRunResult(cells={"answer": "x"})
    else:
        run = CellRunResult(declined_cause=state)
    return (
        land_row(
            _ctx(fill, task),
            claimed.row_id,
            run,
            flow=flow,
            task_id=str(claimed.id),
            lists=_SheetThatTakesEverything(account_id=ACCOUNT),
        )
        is not None
    )


class TerminalWriteTests(TestCase):
    def test_terminal_write_lands_the_diagnosis(self) -> None:
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        self.assertTrue(land(fill, task, state=StoredCellState.NO_EVIDENCE))
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.DONE)
        cell = ListCellState.objects.get()
        self.assertEqual(
            (cell.list_id, cell.row_id, cell.column_key, cell.state, cell.fill_run_id),
            (LIST, task.row_id, "answer", StoredCellState.NO_EVIDENCE, str(fill.id)),
        )

    def test_a_reclaimed_tasks_original_claimant_misses_and_lands_nothing(self) -> None:
        # A claim that went stale is reclaimed to READY; the reclaiming
        # worker owns the next write, and the original claimant's
        # settle CAS (on its own stamp) misses, rolling the diagnosis
        # back with it.
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        original = NodeRunFlow(worker_id="test:1")
        claimed = original.claim(str(task.id))
        NodeRun.objects.filter(id=task.id).update(
            last_state_change_at=timezone.now() - datetime.timedelta(seconds=PROCESSING_STALE_SECONDS + 60)
        )
        self.assertEqual(NodeRunFlow.reclaim_stale_processing(), 1)
        # The original claimant's terminal write now misses.
        self.assertFalse(
            land_row(
                _ctx(fill, task),
                claimed.row_id,
                CellRunResult(declined_cause=StoredCellState.NO_EVIDENCE),
                flow=original,
                task_id=str(claimed.id),
                lists=_SheetThatTakesEverything(account_id=ACCOUNT),
            )
            is not None
        )
        self.assertFalse(ListCellState.objects.exists())

    def test_an_answered_column_overwrites_its_earlier_blank(self) -> None:
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        land(fill, task, state=StoredCellState.NO_EVIDENCE)
        self.assertEqual(ListCellState.objects.get().state, StoredCellState.NO_EVIDENCE)
        later = make_run(rows=1)
        NodeRun.objects.filter(fill_run_id=str(later.id)).update(row_id=task.row_id)
        second = NodeRun.objects.get(fill_run_id=str(later.id))
        self.assertTrue(land(later, second))
        self.assertEqual(ListCellState.objects.count(), 1)
        self.assertEqual(ListCellState.objects.get().state, StoredCellState.FILLED)


class CompletionTests(TestCase):
    """The fill job's poll IS the completion rule: once its target set
    is whole, each tick asks whether any run is still open and parks
    (no attempt spent) while one is; the tick that finds none settles
    the job DONE, and the fill reads complete."""

    def _drain(self, fill: Job) -> None:
        for task in list(NodeRun.objects.filter(fill_run_id=str(fill.id), status=NodeRunStatus.READY)):
            land(fill, task)

    def test_the_poll_leaves_a_fill_with_work_remaining_open(self) -> None:
        fill = make_run(rows=2)
        task = NodeRun.objects.filter(fill_run_id=str(fill.id)).order_by("rank", "id").first()
        land(fill, task)
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.attempts), (JobStatus.READY, 0))
        self.assertIsNotNone(fill.scheduled_at)  # parked until its next look
        self.assertEqual(fill_status(str(fill.id)), "running")

    def test_the_poll_completes_a_drained_fill(self) -> None:
        fill = make_run(rows=2)
        self._drain(fill)
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.error), (JobStatus.DONE, ""))
        self.assertEqual(fill_status(str(fill.id)), "complete")

    def test_a_drained_open_fill_is_still_offered_to_the_provisioner(self) -> None:
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        land(fill, task)
        self.assertEqual([j.id for j in fill_progress.iter_open_fills()], [fill.id])
        tick_fill(str(fill.id))
        self.assertEqual(list(fill_progress.iter_open_fills()), [])

    def test_a_processing_task_blocks_completion(self) -> None:
        # A task a consumer owns (PROCESSING) is still owed, so a crashed
        # claimant never fakes completion.
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        NodeRunFlow(worker_id="test:1").claim(str(task.id))
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual(fill.status, JobStatus.READY)

    def test_a_parked_task_blocks_completion(self) -> None:
        fill = make_run(rows=1)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        flow = NodeRunFlow(worker_id="test:1")
        flow.claim(str(task.id))
        flow.park(str(task.id), backoff_seconds=60, result={})
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual(fill.status, JobStatus.READY)

    def test_fail_fill_is_cas_from_open_states(self) -> None:
        fill = make_run(rows=1)
        self.assertTrue(fill_progress.fail(str(fill.id), code="model_unrunnable", message="why"))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.error_code, fill.error), (JobStatus.FAILED, "model_unrunnable", "why"))
        self.assertEqual(fill_status(str(fill.id)), "failed")
        self.assertFalse(fill_progress.fail(str(fill.id), code="x", message="y"))
        # The tick that comes round finds nothing to hold: a stopped
        # job is never claimed again.
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.error_code), (JobStatus.FAILED, "model_unrunnable"))

    def test_stopping_a_fill_abandons_its_queue_and_touches_no_cell(self) -> None:
        # The record of consent granted and NOT spent. Nothing on the
        # sheet is written or unwritten: those cells were pending only
        # because a non-terminal task said so. The PROCESSING task the
        # worker still owns is left to its own terminal CAS.
        fill = make_run(rows=3)
        first = NodeRun.objects.filter(fill_run_id=str(fill.id)).order_by("rank", "id").first()
        land(fill, first)
        self.assertTrue(fill_progress.cancel(str(fill.id)))
        by_status = dict(
            NodeRun.objects.filter(fill_run_id=str(fill.id)).values_list("status").annotate(n=models.Count("id"))
        )
        self.assertEqual(by_status, {NodeRunStatus.DONE: 1, NodeRunStatus.ABANDONED: 2})
        self.assertEqual(ListCellState.objects.count(), 1)

    def test_a_stop_while_a_tick_holds_the_job_stands(self) -> None:
        # The runner's park is predicated on PROCESSING: a cancel that
        # lands while a tick is mid-poll (one run still open, so the
        # slice parks) flips it, and the tick's own park misses instead
        # of resurrecting it to READY with a wake. FAILS with the park's
        # predicate gone: the fill would read pending again and settle
        # DONE on the next tick.
        fill = make_run(rows=1)

        def cancel_mid_poll(fill_run_id: str) -> bool:
            # The cancel lands, and the slice's read of its runs is the
            # stale one it already took: still open, so it parks.
            fill_progress.cancel(fill_run_id)
            return True

        with patch("lists.jobs.fill.NodeRunFlow.has_open_for_fill", side_effect=cancel_mid_poll):
            tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.scheduled_at), (JobStatus.CANCELLED, None))
        self.assertEqual(
            set(NodeRun.objects.filter(fill_run_id=str(fill.id)).values_list("status", flat=True)),
            {NodeRunStatus.ABANDONED},
        )
        tick_fill(str(fill.id))
        fill.refresh_from_db()
        self.assertEqual(fill.status, JobStatus.CANCELLED)

    def test_the_reclaim_abandons_a_closed_fills_queued_runs_and_leaves_an_open_ones(self) -> None:
        # A fill closed with no sweep (a stop that died between its flip
        # and its tidy), an open fill beside it: one judgement, paged one
        # fill id at a time, abandons the closed one's runs only.
        closed = make_run(rows=2)
        Job.objects.filter(id=closed.id).update(status=JobStatus.CANCELLED)
        open_fill = make_run(rows=2)
        self.assertEqual(NodeRunFlow.abandon_orphans(batch=1), 2)
        self.assertEqual(
            set(NodeRun.objects.filter(fill_run_id=str(closed.id)).values_list("status", flat=True)),
            {NodeRunStatus.ABANDONED},
        )
        self.assertEqual(
            set(NodeRun.objects.filter(fill_run_id=str(open_fill.id)).values_list("status", flat=True)),
            {NodeRunStatus.READY},
        )
        # Idempotent: nothing left to judge.
        self.assertEqual(NodeRunFlow.abandon_orphans(), 0)

    def test_the_consent_reader_is_typed_and_refuses_a_drifted_payload(self) -> None:
        # One reader of the payload: a field the kind renamed fails here,
        # loudly, instead of a guard reading "no keys" and waving a
        # second fill onto a live column.
        fill = make_run(rows=1)
        (read,) = list(fill_progress.iter_consents(fill_progress.fill_jobs().filter(id=fill.id)))
        self.assertEqual((read[0], read[1].column_keys), (str(fill.id), ["answer"]))
        Job.objects.filter(id=fill.id).update(payload={"list_id": LIST})
        with self.assertRaises(ValidationError):
            list(fill_progress.iter_consents(fill_progress.fill_jobs().filter(id=fill.id)))

    def test_counters_derive_from_tasks_and_cells(self) -> None:
        fill = make_run(rows=2)
        tasks = list(NodeRun.objects.filter(fill_run_id=str(fill.id)).order_by("rank", "id"))
        land(fill, tasks[0])  # FILLED
        land(fill, tasks[1], state=StoredCellState.NO_EVIDENCE)  # blank
        counters = page_progress([fill])[str(fill.id)].counters
        self.assertEqual(
            (counters.attempted, counters.filled, counters.blank, counters.transient),
            (2, 1, 1, 0),
        )


class RunControlTests(TestCase):
    def test_cancel_flips_an_open_run_and_noops_terminal(self) -> None:
        fill = make_run(rows=1)
        service = FillService(account_id=ACCOUNT)
        cancelled = service.cancel(str(fill.id))
        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertEqual(fill_status(str(fill.id)), "cancelled")
        self.assertEqual(service.cancel(str(fill.id)).status, JobStatus.CANCELLED)

    def test_a_cancelled_run_leaves_no_open_fill(self) -> None:
        fill = make_run(rows=1)
        self.assertEqual([f.id for f in fill_progress.iter_open_fills()], [fill.id])
        FillService(account_id=ACCOUNT).cancel(str(fill.id))
        self.assertEqual(list(fill_progress.iter_open_fills()), [])

    def test_foreign_account_reads_as_not_found(self) -> None:
        fill = make_run(rows=1)
        foreign = FillService(account_id="01FOREIGNAAAAAAAAAAAAAAAAA")
        with self.assertRaises(FillNotFound):
            foreign.get(str(fill.id))
        with self.assertRaises(FillNotFound):
            foreign.cancel(str(fill.id))

    def test_page_for_list_keysets_open_runs_only(self) -> None:
        fills = [make_run(rows=1) for _ in range(3)]
        cancelled = fills[0]
        FillService(account_id=ACCOUNT).cancel(str(cancelled.id))
        live_newest_first = sorted((str(fill.id) for fill in fills[1:]), reverse=True)
        service = FillService(account_id=ACCOUNT)
        page = service.page_for_list(LIST, after_id="", limit=1)
        self.assertEqual([str(fill.id) for fill in page], live_newest_first[:1])
        rest = service.page_for_list(LIST, after_id=str(page[-1].id), limit=2)
        self.assertEqual([str(fill.id) for fill in rest], live_newest_first[1:])
        self.assertNotIn(str(cancelled.id), [str(fill.id) for fill in (*page, *rest)])

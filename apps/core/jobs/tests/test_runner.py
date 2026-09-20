"""The job runner: a kind's slices under the tick's budget, the cursor
durable after each, the CAS that keeps two ticks off one job, the
backoff and the cap when a slice raises, the reclaim of a dead tick's
job, and the registry's guards. The kind under test is registered here
and counts what it was asked to do.

Run: DJANGO_ENV=test uv run python manage.py test jobs
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from pydantic import BaseModel, ValidationError

from ..constants import JOB_ATTEMPTS, JOB_LOOP_IDLE_SECONDS, JOB_RETRY_BACKOFF_SECONDS, JOB_STALE_SECONDS, JobStatus
from ..kinds import registry
from ..kinds.base import JobFailed, JobKind, Wait
from ..kinds.registry import all_kinds, register
from ..models import Job
from ..services import JobRunner, cancel, enqueue, fail, stop
from ..services.loop import run_loop
from ..services.runner import EXHAUSTED

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
SLICES: list[tuple[str, int]] = []


STOPPED: list[str] = []


class Counting(JobKind):
    """Walks `pages` slices, recording each; raises on the slice named
    by `boom`; answers `Wait` on the slice named by `wait_at` (once,
    for `wait_seconds`); raises JobFailed on the slice named by
    `verdict_at`; records a stop from outside."""

    KIND: ClassVar[str] = "test_counting"
    pages: int
    boom: int = -1
    wait_at: int = -1
    wait_seconds: int = 0
    verdict_at: int = -1

    class Progress(BaseModel):
        done: int = 0
        waited: bool = False

    def run(self, job: Job, progress: Progress) -> Progress | Wait | None:
        if progress.done == self.boom:
            raise RuntimeError("slice exploded")
        if progress.done == self.verdict_at:
            raise JobFailed("test_verdict", "the kind decided")
        if progress.done == self.wait_at and not progress.waited:
            return Wait(self.wait_seconds, self.Progress(done=progress.done, waited=True))
        if progress.done >= self.pages:
            return None
        SLICES.append((str(job.id), progress.done))
        return self.Progress(done=progress.done + 1, waited=progress.waited)

    def on_stop(self, job: Job) -> None:
        STOPPED.append(str(job.id))


register(Counting)


class RunnerTests(TestCase):
    def setUp(self) -> None:
        SLICES.clear()
        STOPPED.clear()
        self.runner = JobRunner(worker_id="tick:1")

    def test_a_wait_parks_the_job_until_it_asked_to_be_woken_and_spends_no_attempt(self):
        job = enqueue(ACCOUNT, Counting(pages=2, wait_at=1, wait_seconds=600))
        before = timezone.now()
        report = self.runner.tick()
        job.refresh_from_db()
        # Slice 0 ran, slice 1 answered Wait: parked READY with its
        # cursor, due after the wait, no attempt, no cause written.
        self.assertEqual((report.parked, job.status, job.attempts, job.error), (1, JobStatus.READY, 0, ""))
        self.assertEqual(job.progress, {"done": 1, "waited": True})
        self.assertGreaterEqual(job.scheduled_at, before + timedelta(seconds=600))
        self.assertEqual(self.runner.tick().claimed, 0)  # not due yet
        Job.objects.filter(id=job.id).update(scheduled_at=None)
        self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((job.status, job.progress), (JobStatus.DONE, {"done": 2, "waited": True}))

    def test_a_kinds_own_verdict_fails_the_job_with_its_code_and_no_attempt(self):
        job = enqueue(ACCOUNT, Counting(pages=3, verdict_at=1))
        report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((report.failed, job.status, job.attempts), (1, JobStatus.FAILED, 0))
        self.assertEqual((job.error_code, job.error), ("test_verdict", "the kind decided"))
        self.assertIsNotNone(job.settled_at)

    def test_a_budget_park_leaves_the_stored_cause_alone(self):
        # A raising slice writes its cause; a later park for budget must
        # not blank it (a kind waiting on something outside the job
        # reads it back on its next slice).
        job = enqueue(ACCOUNT, Counting(pages=3))
        Job.objects.filter(id=job.id).update(error="an earlier cause")
        self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        self.assertEqual((job.status, job.error), (JobStatus.READY, "an earlier cause"))

    def test_a_stop_from_outside_tidies_through_the_kind_then_flips(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        self.assertTrue(cancel(str(job.id)))
        job.refresh_from_db()
        self.assertEqual((job.status, STOPPED), (JobStatus.CANCELLED, [str(job.id)]))
        self.assertIsNotNone(job.settled_at)
        # Terminal already: a second stop is a no-op, and no tick claims it.
        self.assertFalse(fail(str(job.id), code="x", message="y"))
        self.assertEqual(self.runner.tick().claimed, 0)
        job.refresh_from_db()
        self.assertEqual((job.status, job.error_code), (JobStatus.CANCELLED, ""))

    def test_a_fail_from_outside_carries_both_legs(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        self.assertTrue(fail(str(job.id), code="model_unrunnable", message="The model could not be reached."))
        job.refresh_from_db()
        self.assertEqual(
            (job.status, job.error_code, job.error),
            (JobStatus.FAILED, "model_unrunnable", "The model could not be reached."),
        )

    def test_a_stop_while_a_tick_holds_the_job_is_never_resurrected(self):
        # The runner's park and settle are predicated on PROCESSING: a
        # job stopped while held stays stopped, and the tick's own
        # write misses.
        job = enqueue(ACCOUNT, Counting(pages=1))
        Job.objects.filter(id=job.id).update(status=JobStatus.PROCESSING)
        self.assertTrue(stop(str(job.id), status=JobStatus.CANCELLED))
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertEqual(JobRunner._settle(job), 0)

    def test_a_job_runs_its_slices_to_done_within_one_tick(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        self.assertEqual((job.status, job.kind), (JobStatus.READY, "test_counting"))
        self.assertEqual(job.payload["pages"], 3)

        report = self.runner.tick()

        self.assertEqual((report.claimed, report.done, report.parked, report.failed), (1, 1, 0, 0))
        self.assertEqual(SLICES, [(str(job.id), 0), (str(job.id), 1), (str(job.id), 2)])
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.progress["done"], job.error), (JobStatus.DONE, 0, 3, ""))
        self.assertIsNotNone(job.settled_at)

    def test_the_budget_parks_a_job_with_its_cursor_and_the_next_tick_resumes_it(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        # A zero budget: one slice, then park. FAILS if the cursor is
        # not durable between ticks (the second tick would restart).
        first = self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        self.assertEqual((first.parked, job.status, job.progress["done"]), (1, JobStatus.READY, 1))
        self.assertLessEqual(job.scheduled_at, timezone.now())

        second = self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        # Running out of tick is not an attempt.
        self.assertEqual((second.parked, job.progress["done"], job.attempts), (1, 2, 0))
        self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((job.status, job.progress["done"], job.attempts), (JobStatus.DONE, 3, 0))
        self.assertEqual([done for _, done in SLICES], [0, 1, 2])

    def test_a_job_needing_more_ticks_than_the_cap_allows_attempts_still_finishes(self):
        # FAILS if a budget park counts as an attempt: the sixth claim
        # would fail the job as exhausted with nothing wrong.
        job = enqueue(ACCOUNT, Counting(pages=JOB_ATTEMPTS + 3))
        for _ in range(JOB_ATTEMPTS + 3):
            self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.progress["done"]), (JobStatus.READY, 0, JOB_ATTEMPTS + 3))
        self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((job.status, job.error), (JobStatus.DONE, ""))

    def test_a_budget_park_leaves_the_queue_to_the_next_tick(self):
        # One tick budget shared by every job: the long job at the front
        # parks on it and the tick ends; the job behind waits a minute.
        first = enqueue(ACCOUNT, Counting(pages=2))
        second = enqueue(ACCOUNT, Counting(pages=1))
        report = self.runner.tick(budget_seconds=0)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((report.claimed, first.status, second.status), (1, JobStatus.READY, JobStatus.READY))
        self.assertEqual([done for _, done in SLICES], [0])

    def test_the_cursor_is_durable_after_every_slice_not_only_at_the_park(self):
        job = enqueue(ACCOUNT, Counting(pages=2, boom=1))
        self.runner.tick()
        job.refresh_from_db()
        # Slice 0 landed its cursor before slice 1 raised.
        self.assertEqual(job.progress["done"], 1)

    def test_a_raising_slice_parks_with_its_cause_and_a_backoff(self):
        job = enqueue(ACCOUNT, Counting(pages=2, boom=0))
        before = timezone.now()
        with self.assertLogs("jobs.services.runner", level="ERROR"):
            report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((report.parked, job.status, job.attempts), (1, JobStatus.READY, 1))
        self.assertEqual(job.error, "RuntimeError: slice exploded")
        self.assertGreaterEqual(job.scheduled_at, before + timedelta(seconds=JOB_RETRY_BACKOFF_SECONDS))
        # Not due yet: the next tick leaves it alone.
        self.assertEqual(self.runner.tick().claimed, 0)

    def test_the_raise_that_reaches_the_cap_fails_the_job_with_its_cause(self):
        job = enqueue(ACCOUNT, Counting(pages=2, boom=0))
        Job.objects.filter(id=job.id).update(attempts=JOB_ATTEMPTS - 1)
        with self.assertLogs("jobs.services.runner", level="ERROR"):
            report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((report.failed, job.status, job.attempts), (1, JobStatus.FAILED, JOB_ATTEMPTS))
        self.assertEqual(job.error, "RuntimeError: slice exploded")
        self.assertIsNotNone(job.settled_at)

    def test_a_job_at_the_cap_that_runs_clean_still_finishes(self):
        # The cap is on failures, not on claims: a job whose earlier
        # exits were unexpected but whose next slice works, works.
        job = enqueue(ACCOUNT, Counting(pages=1))
        Job.objects.filter(id=job.id).update(attempts=JOB_ATTEMPTS - 1)
        report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((report.done, job.status, job.attempts), (1, JobStatus.DONE, JOB_ATTEMPTS - 1))

    def test_two_ticks_never_hold_one_job(self):
        job = enqueue(ACCOUNT, Counting(pages=1))
        Job.objects.filter(id=job.id).update(status=JobStatus.PROCESSING)  # a live tick holds it
        report = JobRunner(worker_id="tick:2").tick()
        self.assertEqual((report.claimed, SLICES), (0, []))

    def test_a_dead_ticks_job_is_reclaimed_and_resumes_from_its_cursor(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        stale = timezone.now() - timedelta(seconds=JOB_STALE_SECONDS + 60)
        Job.objects.filter(id=job.id).update(
            status=JobStatus.PROCESSING, progress={"done": 2}, attempts=1, last_state_change_at=stale
        )
        report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((report.reclaimed, report.done, job.status, job.attempts), (1, 1, JobStatus.DONE, 2))
        self.assertEqual([done for _, done in SLICES], [2])

    def test_a_dead_tick_at_the_cap_fails_the_job(self):
        # The dead tick is the one unexpected exit no except block sees:
        # the reclaim counts it, and at the cap it stops the job.
        job = enqueue(ACCOUNT, Counting(pages=3))
        stale = timezone.now() - timedelta(seconds=JOB_STALE_SECONDS + 60)
        Job.objects.filter(id=job.id).update(
            status=JobStatus.PROCESSING, attempts=JOB_ATTEMPTS - 1, last_state_change_at=stale
        )
        report = self.runner.tick()
        job.refresh_from_db()
        self.assertEqual(
            (report.reclaimed, job.status, job.attempts, job.error), (1, JobStatus.FAILED, JOB_ATTEMPTS, EXHAUSTED)
        )
        self.assertEqual(SLICES, [])

    def test_a_live_processing_job_is_not_reclaimed(self):
        job = enqueue(ACCOUNT, Counting(pages=1))
        Job.objects.filter(id=job.id).update(status=JobStatus.PROCESSING, last_state_change_at=timezone.now())
        self.assertEqual(self.runner.tick().reclaimed, 0)

    def test_an_unknown_kind_parks_with_its_cause_rather_than_crashing_the_tick(self):
        job = enqueue(ACCOUNT, Counting(pages=1))
        Job.objects.filter(id=job.id).update(kind="test_retired")
        later = enqueue(ACCOUNT, Counting(pages=1))
        with self.assertLogs("jobs.services.runner", level="ERROR"):
            report = self.runner.tick()
        job.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual((report.parked, report.done), (1, 1))
        self.assertIn("KeyError", job.error)
        self.assertEqual(later.status, JobStatus.DONE)

    def test_the_command_runs_one_tick_and_logs_the_report(self):
        enqueue(ACCOUNT, Counting(pages=1))
        with self.assertLogs("jobs.services.loop", level="INFO") as logs:
            call_command("run_jobs", "--once")
        self.assertIn("done=1", logs.output[0])


class LoopTests(TestCase):
    """The process loop: ticks until stopped, idles only when a tick
    found nothing, and a stop set mid-tick ends it after that tick."""

    def setUp(self) -> None:
        SLICES.clear()

    def test_a_stop_already_set_ends_the_loop_before_any_tick(self):
        enqueue(ACCOUNT, Counting(pages=1))
        stop = threading.Event()
        stop.set()
        run_loop(JobRunner(worker_id="loop:1"), stop=stop)
        self.assertEqual(SLICES, [])

    def test_the_loop_works_a_backlog_without_idling_and_idles_once_empty(self):
        # Two jobs: the first tick works both (no idle between), the
        # next tick finds nothing and idles; the idle wait is what the
        # stop interrupts.
        enqueue(ACCOUNT, Counting(pages=1))
        enqueue(ACCOUNT, Counting(pages=1))
        stop = threading.Event()
        waits: list[float] = []

        def wait(seconds: float) -> bool:
            waits.append(seconds)
            stop.set()
            return True

        stop.wait = wait  # type: ignore[method-assign]
        run_loop(JobRunner(worker_id="loop:1"), stop=stop)
        self.assertEqual(len(SLICES), 2)
        self.assertEqual(waits, [JOB_LOOP_IDLE_SECONDS])

    def test_the_heartbeat_is_touched_each_pass(self):
        stop = threading.Event()
        with patch("jobs.services.loop._touch_heartbeat") as touch:
            run_loop(JobRunner(worker_id="loop:1"), stop=stop, once=True)
        touch.assert_called_once()


class _Cursor(BaseModel):
    pass


class RegistryTests(SimpleTestCase):
    def test_the_lists_kinds_are_on_the_roster_at_boot(self):
        kinds = [cls.KIND for cls in all_kinds()]
        self.assertIn("enqueue_runs", kinds)
        self.assertIn("fill", kinds)

    def test_a_kind_without_run_is_refused(self):
        class NoRun(JobKind):
            KIND: ClassVar[str] = "test_no_run"
            Progress = _Cursor

        with self.assertRaises(ValueError):
            register(NoRun)
        self.assertNotIn("test_no_run", [cls.KIND for cls in all_kinds()])

    def test_a_kind_without_a_progress_model_is_refused(self):
        class NoCursor(JobKind):
            KIND: ClassVar[str] = "test_no_cursor"

            def run(self, job: Job, progress: BaseModel) -> BaseModel | None:
                return None

        with self.assertRaises(ValueError):
            register(NoCursor)
        self.assertNotIn("test_no_cursor", [cls.KIND for cls in all_kinds()])

    def test_a_malformed_stored_cursor_refuses_at_the_parse(self):
        with self.assertRaises(ValidationError):
            Counting.Progress.model_validate({"done": "three"})

    def test_a_kind_collision_is_refused_and_re_registration_is_a_no_op(self):
        class Impostor(JobKind):
            KIND: ClassVar[str] = Counting.KIND
            Progress = _Cursor

            def run(self, job: Job, progress: BaseModel) -> BaseModel | None:
                return None

        with self.assertRaises(ValueError):
            register(Impostor)
        register(Counting)
        self.assertIs(registry.get(Counting.KIND), Counting)

    def test_a_kind_name_past_the_bound_is_refused(self):
        class Long(JobKind):
            KIND: ClassVar[str] = "k" * 33
            Progress = _Cursor

            def run(self, job: Job, progress: BaseModel) -> BaseModel | None:
                return None

        with self.assertRaises(ValueError), patch.dict(registry._REGISTRY, {}, clear=False):
            register(Long)

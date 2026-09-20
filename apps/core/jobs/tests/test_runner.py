"""The job runner: a kind's slices under the tick's budget, the cursor
durable after each, the CAS that keeps two ticks off one job, the
backoff and the cap when a slice raises, the reclaim of a dead tick's
job, and the registry's guards. The kind under test is registered here
and counts what it was asked to do.

Run: DJANGO_ENV=test uv run python manage.py test jobs
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ..constants import JOB_ATTEMPTS, JOB_RETRY_BACKOFF_SECONDS, JOB_STALE_SECONDS, JobStatus
from ..kinds import registry
from ..kinds.base import JobKind
from ..kinds.registry import all_kinds, register
from ..models import Job
from ..services import JobRunner, enqueue
from ..services.runner import EXHAUSTED

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
SLICES: list[tuple[str, int]] = []


class Counting(JobKind):
    """Walks `pages` slices, recording each; raises on the slice named
    by `boom`."""

    KIND: ClassVar[str] = "test_counting"
    pages: int
    boom: int = -1

    def run(self, job: Job) -> dict | None:
        done = int(job.progress.get("done", 0))
        if done == self.boom:
            raise RuntimeError("slice exploded")
        if done >= self.pages:
            return None
        SLICES.append((str(job.id), done))
        return {"done": done + 1}


register(Counting)


class RunnerTests(TestCase):
    def setUp(self) -> None:
        SLICES.clear()
        self.runner = JobRunner(worker_id="tick:1")

    def test_a_job_runs_its_slices_to_done_within_one_tick(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        self.assertEqual(
            (job.status, job.kind, job.payload), (JobStatus.READY, "test_counting", {"pages": 3, "boom": -1})
        )

        report = self.runner.tick()

        self.assertEqual((report.claimed, report.done, report.parked, report.failed), (1, 1, 0, 0))
        self.assertEqual(SLICES, [(str(job.id), 0), (str(job.id), 1), (str(job.id), 2)])
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.progress, job.error), (JobStatus.DONE, 0, {"done": 3}, ""))
        self.assertIsNotNone(job.settled_at)

    def test_the_budget_parks_a_job_with_its_cursor_and_the_next_tick_resumes_it(self):
        job = enqueue(ACCOUNT, Counting(pages=3))
        # A zero budget: one slice, then park. FAILS if the cursor is
        # not durable between ticks (the second tick would restart).
        first = self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        self.assertEqual((first.parked, job.status, job.progress), (1, JobStatus.READY, {"done": 1}))
        self.assertLessEqual(job.scheduled_at, timezone.now())

        second = self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        # Running out of tick is not an attempt.
        self.assertEqual((second.parked, job.progress, job.attempts), (1, {"done": 2}, 0))
        self.runner.tick()
        job.refresh_from_db()
        self.assertEqual((job.status, job.progress, job.attempts), (JobStatus.DONE, {"done": 3}, 0))
        self.assertEqual([done for _, done in SLICES], [0, 1, 2])

    def test_a_job_needing_more_ticks_than_the_cap_allows_attempts_still_finishes(self):
        # FAILS if a budget park counts as an attempt: the sixth claim
        # would fail the job as exhausted with nothing wrong.
        job = enqueue(ACCOUNT, Counting(pages=JOB_ATTEMPTS + 3))
        for _ in range(JOB_ATTEMPTS + 3):
            self.runner.tick(budget_seconds=0)
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts, job.progress), (JobStatus.READY, 0, {"done": JOB_ATTEMPTS + 3}))
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
        self.assertEqual(job.progress, {"done": 1})

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

    def test_the_command_runs_a_tick_and_logs_the_report(self):
        enqueue(ACCOUNT, Counting(pages=1))
        with self.assertLogs("jobs.management.commands.run_jobs", level="INFO") as logs:
            call_command("run_jobs")
        self.assertIn("done=1", logs.output[0])


class RegistryTests(SimpleTestCase):
    def test_the_lists_backfill_kind_is_on_the_roster_at_boot(self):
        self.assertIn("webhook_backfill", [cls.KIND for cls in all_kinds()])

    def test_a_kind_without_run_is_refused(self):
        class NoRun(JobKind):
            KIND: ClassVar[str] = "test_no_run"

        with self.assertRaises(ValueError):
            register(NoRun)
        self.assertNotIn("test_no_run", [cls.KIND for cls in all_kinds()])

    def test_a_kind_collision_is_refused_and_re_registration_is_a_no_op(self):
        class Impostor(JobKind):
            KIND: ClassVar[str] = Counting.KIND

            def run(self, job: Job) -> dict | None:
                return None

        with self.assertRaises(ValueError):
            register(Impostor)
        register(Counting)
        self.assertIs(registry.get(Counting.KIND), Counting)

    def test_a_kind_name_past_the_bound_is_refused(self):
        class Long(JobKind):
            KIND: ClassVar[str] = "k" * 33

            def run(self, job: Job) -> dict | None:
                return None

        with self.assertRaises(ValueError), patch.dict(registry._REGISTRY, {}, clear=False):
            register(Long)

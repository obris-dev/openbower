"""Test-run persistence: the poll lifecycle's ORM custody, so views
stay thin dispatch and the runtime stays persistence-free. Runs are
throwaway diagnostics: stale ones purge opportunistically on start
(age-based only, never read-side; a live poll loop must never lose its
run to a sibling's POST)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from lists.constants import MAX_ACTIVE_FILLS
from lists.services.fill_progress import live_fill_count
from openbower_kernel.fields import min_ulid_at
from openbower_schema.agents import AgentConfig
from openbower_schema.fills import CellRunResult

from ..constants import (
    TEST_RUN_ABANDON_SECONDS,
    TEST_RUN_ERROR_MAX_LENGTH,
    TEST_RUN_MAX_AGE_SECONDS,
    TEST_RUN_WORST_CASE_SECONDS,
    TestRunStatus,
)
from ..models import AgentTestRun


class TestRunNotFound(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"no test run {run_id}")


class PaidLanesFull(Exception):
    """The account cap counts BOTH metered lanes: live fills plus
    pending bench runs young enough to still be live. One cap, one
    constant, and neither lane counts the other's rows by reaching
    into its models."""

    def __init__(self) -> None:
        super().__init__(
            f"This account is at its limit of {MAX_ACTIVE_FILLS} concurrent fills and tests; wait for one to finish."
        )


class TestRunActive(Exception):
    """One live run per account: the test endpoint is a metered path
    (searches + completions per run), and until phase 5's fill
    machinery brings real spend controls, a young pending run refuses
    a second start. Your OWN run is superseded instead, but only past
    a short abandonment window (its thread may still be spending;
    instant supersede would fork concurrent paid runs on every
    reload-and-retest)."""


def config_fingerprint(config: AgentConfig) -> str:
    """The canonical digest of a drafted config: what makes a stored
    bench run SEEDABLE by a fill admission (seed only when the
    admitted config is THIS config; a near-miss must re-run, never
    borrow answers produced by different instructions)."""
    return hashlib.sha256(json.dumps(config.model_dump(), sort_keys=True).encode("utf-8")).hexdigest()


class TestRunService:
    """Account-scoped reads and starts; terminal writes key on the run
    id alone (the worker thread owns no request context)."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def assert_lane_available(self) -> None:
        """Check-then-create, like the start guard's own create-create
        window; the per-process test-thread semaphore backstops the
        residual race. Liveness is judged by the WORST CASE here
        exactly as the start guard judges it, so an older pending row
        that provably finished never locks the account out."""
        pending = AgentTestRun.objects.filter(
            account_id=self.account_id,
            status=TestRunStatus.PENDING,
            id__gte=min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_WORST_CASE_SECONDS)),
        ).count()
        if live_fill_count(self.account_id) + pending >= MAX_ACTIVE_FILLS:
            raise PaidLanesFull()

    def start(self, *, user_id: str, config_fingerprint: str = "", row_id: str = "") -> AgentTestRun:
        # Age filters ride the (account_id, id) index via the ULID's
        # time prefix; created_at has no index of its own.
        AgentTestRun.objects.filter(
            account_id=self.account_id,
            id__lt=min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_MAX_AGE_SECONDS)),
        ).delete()
        return self._guarded_create(user_id=user_id, config_fingerprint=config_fingerprint, row_id=row_id)

    @transaction.atomic
    def _guarded_create(self, *, user_id: str, config_fingerprint: str, row_id: str) -> AgentTestRun:
        # select_for_update serializes concurrent starts against the
        # SAME pending rows (the supersede check-then-create). Two
        # simultaneous starts with NO pending row can still both
        # create; that residual window is bounded by the per-process
        # test-thread semaphore, not worth an advisory lock here.
        now = timezone.now()
        pending = list(
            AgentTestRun.objects.select_for_update()
            .filter(
                account_id=self.account_id,
                status=TestRunStatus.PENDING,
                # The WORST CASE bounds what can still be live: an
                # older pending row provably finished or died, and
                # blocking on it would be pure dead lockout (the
                # larger stale window is the poll leg's PRESENTATION
                # concern, not a liveness bound).
                id__gte=min_ulid_at(now - timedelta(seconds=TEST_RUN_WORST_CASE_SECONDS)),
            )
            .order_by("-id")
        )
        # LIVE runs block (theirs or yours: one live run per account);
        # abandonment is judged the SAME for both, so a dead
        # teammate's orphan never locks the account either.
        live = [run for run in pending if not self._abandoned(run, now)]
        if live:
            if any(run.user_id != user_id for run in live):
                raise TestRunActive("a test started by a teammate is already running; wait for it to finish")
            raise TestRunActive("your test is still running; wait a moment for it to finish")
        for run in pending:
            # SILENT past the abandonment window: the poll loop that
            # started it is gone (a closed tab); supersede.
            fail_run(str(run.id), "superseded by a newer test")
        return AgentTestRun.objects.create(
            account_id=self.account_id,
            user_id=user_id,
            config_fingerprint=config_fingerprint,
            row_id=row_id,
        )

    @staticmethod
    def _abandoned(run: AgentTestRun, now: datetime) -> bool:
        """Abandonment is OBSERVED silence, never presumed from age: a
        live poll loop stamps polled_at every cadence, so this window
        of quiet means the page that started the run is gone."""
        cutoff = now - timedelta(seconds=TEST_RUN_ABANDON_SECONDS)
        if run.polled_at is not None:
            return run.polled_at < cutoff
        # Never polled: the POST-to-first-poll gap is ~one cadence, so
        # the same window applies from the run's birth (its ULID).
        return str(run.id) < min_ulid_at(cutoff)

    def get(self, run_id: str) -> AgentTestRun:
        try:
            return AgentTestRun.objects.get(id=run_id, account_id=self.account_id)
        except AgentTestRun.DoesNotExist as e:
            raise TestRunNotFound(run_id) from e

    def poll(self, run_id: str, *, user_id: str) -> AgentTestRun:
        """The poll leg's read: fetches AND stamps the abandonment
        signal on a pending row (a live loop stamps every cadence; the
        start guard supersedes on silence). OWNER-scoped: a teammate
        polling by hand must not keep a run whose owner's loop is gone
        reading as live."""
        run = self.get(run_id)
        if run.status == TestRunStatus.PENDING and run.user_id == user_id:
            AgentTestRun.objects.filter(id=str(run.id)).update(polled_at=timezone.now())
        return run

    @staticmethod
    def result_for(run: AgentTestRun) -> CellRunResult | None:
        """A finished run's result as the CONTRACT MODEL, or None if
        there is not one to read.

        The read half of what the writer already does: results go into
        the column through CellRunResult, so they come back out through
        it too, and a caller reasons about typed fields instead of
        probing a JSON blob for keys it hopes are there. A pending or
        failed run has no result by definition, and the model default
        {} is the same absence wearing a different shape."""
        if run.status != TestRunStatus.COMPLETE or not run.result:
            return None
        return CellRunResult.model_validate(run.result)


def run_is_pending(run_id: str) -> bool:
    """The worker's pre-work tombstone check (the work gate runs on
    the view's thread; the ORM lives here)."""
    return AgentTestRun.objects.filter(id=run_id, status=TestRunStatus.PENDING).exists()


def complete_run(run_id: str, result: dict) -> None:
    # status=PENDING gate: a superseded run's zombie thread must not
    # overwrite its own tombstone with results from a config the user
    # already replaced. Terminal states are terminal.
    AgentTestRun.objects.filter(id=run_id, status=TestRunStatus.PENDING).update(
        status=TestRunStatus.COMPLETE, result=result, updated_at=timezone.now()
    )


def fail_run(run_id: str, error: str) -> None:
    AgentTestRun.objects.filter(id=run_id, status=TestRunStatus.PENDING).update(
        status=TestRunStatus.FAILED, error=error[:TEST_RUN_ERROR_MAX_LENGTH], updated_at=timezone.now()
    )

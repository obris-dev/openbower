"""The agent kind's processor, one rule per walk mode, and the walker
over a fill scope: the consent range, the limit, the settle at the end,
the cancel mid-walk. The fill here is built by hand around a frozen
config so the judgement is pinned without an admission.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from django.test import TestCase

from common.testing import TEST_IDENTITY
from jobs.models import Job
from jobs.services import JobRunner, enqueue
from lists.constants import CellSource, FillStatus, NodeRunStatus, StoredCellState
from lists.jobs.enqueue_runs import EnqueueRuns
from lists.models import Fill, NodeRun
from lists.nodes.registry import COLUMN_AGENT
from lists.processors import WalkMode, WalkScope, processor_for
from lists.processors.column_agent import AIColumnProcessor
from lists.services import cell_truth, fill_progress
from lists.services.fingerprint import config_fingerprint
from lists.services.lists import ListService
from lists.services.workflows import WorkflowService
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools

ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]
AGENT = "01AGT" + "A" * 21
NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def _config(prompt: str = "Find the answer for {{company}}") -> AgentConfig:
    return AgentConfig(
        prompt=prompt,
        provider="openai_compatible",
        source="ollama",
        model="scripted",
        tools=AgentTools(),
        outputs=[AgentOutput(key="answer", label="Answer", type="text")],
    )


class _Harness(TestCase):
    """A sheet with one agent node filling `answer`, five rows of which
    the fourth has no company (the prompt cannot act on it)."""

    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=AGENT)
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(self.node.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.rows = self.lists.add_rows(
            self.sheet,
            [{"company": "acme.com"}, {"company": "example.io"}, {"company": "acme.org"}, {}, {"company": "last.co"}],
        )

    def _fill(self, config: AgentConfig | None = None, *, status: str = FillStatus.PENDING) -> Fill:
        config = config or _config()
        return Fill.objects.create(
            account_id=ACCOUNT,
            user_id=USER,
            list_id=str(self.sheet.id),
            agent_id=AGENT,
            status=status,
            column_keys=["answer"],
            config_snapshot=config.model_dump(),
            config_fingerprint=config_fingerprint(config),
            confirmed_row_count=5,
        )

    def _processor(self, scope: WalkScope) -> AIColumnProcessor:
        processor = processor_for(account_id=ACCOUNT, node=self.node, scope=scope)
        self.assertIsInstance(processor, AIColumnProcessor)
        return processor

    def _runs(self, fill: Fill | None = None):
        runs = NodeRun.objects.filter(kind=COLUMN_AGENT, node_id=str(self.node.id))
        if fill is not None:
            runs = runs.filter(fill_run_id=str(fill.id))
        return runs.order_by("position")

    def _settle(self, row, state: str, *, fingerprint: str) -> None:
        cell_truth.write(
            account_id=ACCOUNT,
            list_id=str(self.sheet.id),
            row_id=str(row.id),
            fill_run_id=None,
            config_fingerprint=fingerprint,
            states={"answer": state},
            tools={},
            source=CellSource.FILL,
        )


class FreshRuleTests(_Harness):
    def test_a_fresh_walk_queues_the_rows_the_prompt_can_act_on(self):
        fill = self._fill()
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 4)
        runs = list(self._runs(fill))
        self.assertEqual([r.position for r in runs], [1, 2, 3, 5])
        first = runs[0]
        self.assertEqual(
            (first.status, first.kind, first.fill_run_id), (NodeRunStatus.READY, COLUMN_AGENT, str(fill.id))
        )
        self.assertEqual((first.list_id, first.last_state_change_at), (str(self.sheet.id), NOW))
        # Offered again: the open-run key on (fill, row) makes it a no-op.
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 4)
        self.assertEqual(self._runs(fill).count(), 4)

    def test_a_prompt_with_no_variables_acts_on_every_row(self):
        fill = self._fill(_config("Say hello"))
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 5)

    def test_the_probe_finds_the_first_actionable_row_without_queuing(self):
        fill = self._fill()
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.probe(self.sheet), (True, False))
        self.assertEqual(self._runs(fill).count(), 0)
        blank = self._fill(_config("Find {{missing}}"))
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(blank.id)))
        self.assertEqual(processor.probe(self.sheet), (False, True))
        # Within a range that holds no row: nothing found, nothing dropped.
        self.assertEqual(
            self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id))).probe(
                self.sheet, until_position=0
            ),
            (True, False),
        )


class RemainingRuleTests(_Harness):
    def test_settled_under_this_config_and_valued_rows_are_done(self):
        config = _config()
        fill = self._fill(config)
        fp = config_fingerprint(config)
        # Row 1 filled by any config; row 2 a settled blank under THIS
        # config; row 3 a settled blank under ANOTHER config (re-runs);
        # row 5 already holds a value on the sheet.
        self._settle(self.rows[0], StoredCellState.FILLED, fingerprint="other")
        self._settle(self.rows[1], StoredCellState.NO_EVIDENCE, fingerprint=fp)
        self._settle(self.rows[2], StoredCellState.NO_EVIDENCE, fingerprint="other")
        self.lists.write_cells(str(self.sheet.id), str(self.rows[4].id), {"answer": "typed"})
        self.rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        processor = self._processor(WalkScope(mode=WalkMode.REMAINING, fill_run_id=str(fill.id)))
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 1)
        self.assertEqual([r.position for r in self._runs(fill)], [3])
        # Row 4 has no company: dropped, and the probe says so once the
        # one owed row is done too.
        self._settle(self.rows[2], StoredCellState.NO_EVIDENCE, fingerprint=fp)
        self.assertEqual(processor.probe(self.sheet), (False, True))

    def test_a_resume_offers_only_the_rows_the_stopped_fill_still_owed(self):
        config = _config()
        stopped = self._fill(config, status=FillStatus.CANCELLED)
        for row, status in zip(
            self.rows[:3], (NodeRunStatus.DONE, NodeRunStatus.ABANDONED, NodeRunStatus.ABANDONED), strict=True
        ):
            NodeRun.objects.create(
                account_id=ACCOUNT,
                fill_run_id=str(stopped.id),
                node_id=str(self.node.id),
                kind=COLUMN_AGENT,
                row_id=str(row.id),
                list_id=str(self.sheet.id),
                position=row.position,
                status=status,
                last_state_change_at=NOW,
            )
        resumed = self._fill(config)
        scope = WalkScope(mode=WalkMode.REMAINING, fill_run_id=str(resumed.id), owed_by=str(stopped.id))
        processor = self._processor(scope)
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 2)
        self.assertEqual([r.position for r in self._runs(resumed)], [2, 3])


class PushedRuleTests(_Harness):
    def test_a_node_the_push_fully_filled_gets_no_run_and_position_is_stamped(self):
        pushed = self.lists.add_rows(self.sheet, [{"company": "new.io"}, {"company": "done.io", "answer": "sent"}])
        processor = self._processor(WalkScope(mode=WalkMode.PUSHED))
        self.assertEqual(processor.enqueue_runs(self.sheet, pushed, now=NOW), 1)
        (run,) = list(self._runs())
        self.assertEqual((run.row_id, run.position, run.fill_run_id, run.status), (str(pushed[0].id), 6, None, "ready"))


class WalkerOverAFillTests(_Harness):
    """EnqueueRuns with a fill scope: the range, the limit, the settle,
    the cancel."""

    def _walk(self, fill: Fill, **scope) -> Job:
        walk = EnqueueRuns(
            list_id=str(self.sheet.id),
            node_id=str(self.node.id),
            scope=WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id), **scope),
        )
        job = enqueue(ACCOUNT, walk)
        JobRunner(worker_id="test:1").tick()
        job.refresh_from_db()
        return job

    def test_the_range_excludes_a_row_appended_after_consent_and_the_fill_settles(self):
        fill = self._fill()
        self.lists.add_rows(self.sheet, [{"company": "late.io"}])  # position 6, outside the consent
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 2):
            job = self._walk(fill, until_position=5)
        self.assertEqual((job.status, job.progress), ("done", {"after_position": 5, "offered": 4}))
        self.assertEqual([r.position for r in self._runs(fill)], [1, 2, 3, 5])
        fill.refresh_from_db()
        self.assertEqual((fill.confirmed_row_count, fill.status), (4, FillStatus.PENDING))
        self.assertIsNotNone(fill.targeted_at)

    def test_a_scoped_fill_stops_at_its_first_n_qualifying_rows(self):
        fill = self._fill()
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 2):
            job = self._walk(fill, until_position=5, limit=3)
        self.assertEqual(job.status, "done")
        self.assertEqual([r.position for r in self._runs(fill)], [1, 2, 3])
        fill.refresh_from_db()
        self.assertEqual(fill.confirmed_row_count, 3)

    def test_a_fill_that_targets_nothing_completes_when_the_walk_ends(self):
        fill = self._fill(_config("Find {{missing}}"))
        job = self._walk(fill, until_position=5)
        fill.refresh_from_db()
        self.assertEqual((job.status, fill.status, fill.confirmed_row_count), ("done", FillStatus.COMPLETE, 0))

    def test_a_cancel_mid_walk_stops_the_walk_and_leaves_nothing_ready(self):
        fill = self._fill()
        walk = EnqueueRuns(
            list_id=str(self.sheet.id),
            node_id=str(self.node.id),
            scope=WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id), until_position=5),
        )
        job = enqueue(ACCOUNT, walk)
        # One page, then the user cancels before the next slice.
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 2):
            JobRunner(worker_id="test:1").tick(budget_seconds=0)
        self.assertEqual(self._runs(fill).count(), 2)
        self.assertTrue(fill_progress.stop_fill(str(fill.id), FillStatus.CANCELLED))
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.ABANDONED).count(), 2)
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 2):
            JobRunner(worker_id="test:1").tick()
        job.refresh_from_db()
        self.assertEqual((job.status, self._runs(fill).count()), ("done", 2))
        fill.refresh_from_db()
        self.assertEqual((fill.status, fill.targeted_at), (FillStatus.CANCELLED, None))

    def test_a_slice_landing_after_the_cancels_sweep_sweeps_its_own_runs(self):
        # The cancel sweeps what exists, then a slice already past its
        # liveness check inserts more: the slice notices and sweeps them.
        fill = self._fill()
        walk = EnqueueRuns(
            list_id=str(self.sheet.id),
            node_id=str(self.node.id),
            scope=WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id), until_position=5),
        )
        job = enqueue(ACCOUNT, walk)
        real_is_live = fill_progress.is_live
        calls = {"n": 0}

        def cancel_between(fill_run_id: str) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return True  # the pre-insert check passes
            fill_progress.stop_fill(fill_run_id, FillStatus.CANCELLED)
            return real_is_live(fill_run_id)

        with patch("lists.jobs.enqueue_runs.fill_progress.is_live", side_effect=cancel_between):
            JobRunner(worker_id="test:1").tick()
        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.READY).count(), 0)
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.ABANDONED).count(), 4)

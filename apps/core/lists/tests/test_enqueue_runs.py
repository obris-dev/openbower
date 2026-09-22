"""The agent kind's processor (which rows a column_agent node owes a
run, one rule per walk mode, and the probe), and the fill job's walk
over it: the consent range, the scoped limit, the settle of the
denominator, the completion on the same tick, and the cancel mid-walk.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_enqueue_runs
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from django.test import TestCase

from agents.services import AgentService
from common.testing import TEST_IDENTITY
from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner
from lists.constants import AGENT_MISSING_MESSAGE, CellSource, FillFailureCode, NodeRunStatus, StoredCellState
from lists.jobs.fill import FillJob
from lists.models import List, NodeRun
from lists.nodes.registry import COLUMN_AGENT
from lists.processors import WalkMode, WalkScope, processor_for
from lists.processors.column_agent import AIColumnProcessor
from lists.services import cell_truth, fill_progress
from lists.services.lists import ListService
from lists.services.node_runs import NodeRunFlow
from lists.services.workflows import WorkflowService
from openbower_kernel.fields import new_ulid
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools

from .fill_helpers import cursor_of, fill_status, open_fill_job, row_numbers

ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]
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
    """A sheet with one agent node filling `answer` (a real agent, read
    live by the judgement), five rows of which the fourth has no
    company (the prompt cannot act on it)."""

    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.agents = AgentService(account_id=ACCOUNT)
        self.agent = self.agents.create_ephemeral(owner_id=USER, label="Answer", config=_config())
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.node = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=str(self.agent.id))
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(self.node.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.rows = self.lists.add_rows(
            self.sheet,
            [{"company": "acme.com"}, {"company": "example.io"}, {"company": "acme.org"}, {}, {"company": "last.co"}],
        )

    def _prompt(self, prompt: str) -> None:
        """Edit the agent's prompt: the judgement reads it live."""
        self.agents.update(self.agent, config=_config(prompt))

    def _fill(self, *, status: JobStatus = JobStatus.READY, targeted: bool = True, **scope) -> Job:
        return open_fill_job(
            account_id=ACCOUNT,
            user_id=USER,
            list_id=str(self.sheet.id),
            node_id=str(self.node.id),
            agent_id=str(self.agent.id),
            column_keys=["answer"],
            consented=scope.pop("consented", 5),
            status=status,
            targeted=targeted,
            **scope,
        )

    def _processor(self, scope: WalkScope) -> AIColumnProcessor:
        processor = processor_for(account_id=ACCOUNT, node=self.node, scope=scope)
        self.assertIsInstance(processor, AIColumnProcessor)
        return processor

    def _runs(self, fill: Job | None = None):
        runs = NodeRun.objects.filter(kind=COLUMN_AGENT, node_id=str(self.node.id))
        if fill is not None:
            runs = runs.filter(fill_run_id=str(fill.id))
        return runs.order_by("rank", "id")

    def _numbers(self, runs) -> list[int]:
        """The sheet numbers of the runs' rows, in sheet order."""
        numbers = row_numbers(str(self.sheet.id))
        return sorted(numbers[run.row_id] for run in runs)

    def _until(self) -> str:
        """The consent set as admission stores it: an id minted now."""
        return new_ulid()

    def _settle(self, row, state: str) -> None:
        cell_truth.write(
            account_id=ACCOUNT,
            list_id=str(self.sheet.id),
            row_id=str(row.id),
            fill_run_id=None,
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
        self.assertEqual(self._numbers(runs), [1, 2, 3, 5])
        first = runs[0]
        self.assertEqual(
            (first.status, first.kind, first.fill_run_id), (NodeRunStatus.READY, COLUMN_AGENT, str(fill.id))
        )
        self.assertEqual((first.list_id, first.last_state_change_at), (str(self.sheet.id), NOW))
        # Offered again: the open-run key on (fill, row) makes it a no-op.
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 4)
        self.assertEqual(self._runs(fill).count(), 4)

    def test_a_limit_stops_the_queue_and_the_judgement_at_the_nth_qualifying_row(self):
        # Rows 1, 2, 3 and 5 qualify (row 4 has no company). A limit of
        # two queues rows 1 and 2 and judges nothing past row 2: FAILS if
        # the processor judges the page and slices afterwards.
        fill = self._fill()
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        judged: list[str] = []
        real = processor._judge

        def counting(target_list, row, resumed_owed):
            judged.append(row.data.get("company", ""))
            return real(target_list, row, resumed_owed)

        with patch.object(processor, "_judge", side_effect=counting):
            self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW, limit=2), 2)
        self.assertEqual(self._numbers(self._runs(fill)), [1, 2])
        self.assertEqual(judged, ["acme.com", "example.io"])

    def test_a_prompt_with_no_variables_acts_on_every_row(self):
        self._prompt("Say hello")
        fill = self._fill()
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 5)

    def test_the_probe_finds_the_first_actionable_row_without_queuing(self):
        fill = self._fill()
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.probe(self.sheet), (True, False))
        self.assertEqual(self._runs(fill).count(), 0)
        self._prompt("Find {{missing}}")
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.probe(self.sheet), (False, True))
        # Within a consent set that holds no row (an id below every row's):
        # nothing found, nothing dropped.
        self._prompt("Find the answer for {{company}}")
        processor = self._processor(WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id)))
        self.assertEqual(processor.probe(self.sheet, until_id="0" * 26), (False, False))
        # Bounded by the count exactly as the walk is: a fill covering
        # one row finds nothing when that row is the one it cannot act on.
        self.lists.move_row(self.sheet, str(self.rows[3].id), after_id=None)
        self.assertEqual(processor.probe(self.sheet, covered=1), (False, True))
        self.assertEqual(processor.probe(self.sheet, covered=2), (True, True))


class RemainingRuleTests(_Harness):
    def test_only_valued_rows_are_done_and_settled_blanks_re_run(self):
        # Row 1 filled by any run; row 2 a settled blank (re-runs: the
        # click is the consent to re-spend on it); row 3 an
        # infrastructure blank (re-runs); row 5 already holds a value
        # on the sheet. FAILS if a settled blank is skipped again.
        fill = self._fill()
        self.lists.write_cells(str(self.sheet.id), str(self.rows[0].id), {"answer": "answered"})
        self._settle(self.rows[0], StoredCellState.FILLED)
        self._settle(self.rows[1], StoredCellState.NO_EVIDENCE)
        self._settle(self.rows[2], StoredCellState.MODEL_ERROR)
        self.lists.write_cells(str(self.sheet.id), str(self.rows[4].id), {"answer": "typed"})
        self.rows = self.lists.rows_page(self.sheet, limit=10)
        processor = self._processor(WalkScope(mode=WalkMode.REMAINING, fill_run_id=str(fill.id)))
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 2)
        self.assertEqual(self._numbers(self._runs(fill)), [2, 3])
        # Row 4 has no company: dropped, and the probe says so once
        # every owed row holds a value.
        for row in (self.rows[1], self.rows[2]):
            self.lists.write_cells(str(self.sheet.id), str(row.id), {"answer": "now answered"})
        self.assertEqual(processor.probe(self.sheet), (False, True))

    def test_a_resume_offers_only_the_rows_the_stopped_fill_still_owed(self):
        stopped = self._fill(status=JobStatus.CANCELLED)
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
                rank=row.rank,
                status=status,
                last_state_change_at=NOW,
            )
        resumed = self._fill()
        scope = WalkScope(mode=WalkMode.REMAINING, fill_run_id=str(resumed.id), resumed_fill_id=str(stopped.id))
        processor = self._processor(scope)
        self.assertEqual(processor.enqueue_runs(self.sheet, self.rows, now=NOW), 2)
        self.assertEqual(self._numbers(self._runs(resumed)), [2, 3])


class PushedRuleTests(_Harness):
    def test_a_node_the_push_fully_filled_gets_no_run_and_the_rank_is_stamped(self):
        pushed = self.lists.add_rows(self.sheet, [{"company": "new.io"}, {"company": "done.io", "answer": "sent"}])
        processor = self._processor(WalkScope(mode=WalkMode.PUSHED))
        self.assertEqual(processor.enqueue_runs(self.sheet, pushed, now=NOW), 1)
        (run,) = list(self._runs())
        self.assertEqual(
            (run.row_id, run.rank, run.fill_run_id, run.status), (str(pushed[0].id), pushed[0].rank, None, "ready")
        )
        self.assertEqual(self._numbers([run]), [6])

    def test_a_node_filling_no_column_here_owes_a_pushed_row_nothing(self):
        # The node's columns were removed from this sheet: there is
        # nothing to fill, so the judgement says so instead of queuing a
        # run the consumer would only settle unrun. FAILS if the no-
        # column case reads as owed.
        self.sheet.columns = [column for column in self.sheet.columns if column.kind != "ai"]
        self.sheet.save(update_fields=["columns", "updated_at"])
        pushed = self.lists.add_rows(self.sheet, [{"company": "new.io"}])
        processor = self._processor(WalkScope(mode=WalkMode.PUSHED))
        self.assertEqual(processor.enqueue_runs(self.sheet, pushed, now=NOW), 0)
        self.assertFalse(self._runs().exists())


class FillJobWalkTests(_Harness):
    """The fill job's first slices: the range, the limit, the settle of
    the denominator, the completion on the same tick, the cancel."""

    def _walk(self, fill: Job, *, budget: float | None = None) -> Job:
        if budget is None:
            JobRunner(worker_id="test:1").tick()
        else:
            JobRunner(worker_id="test:1").tick(budget_seconds=budget)
        fill.refresh_from_db()
        return fill

    def test_the_range_excludes_a_row_appended_after_consent_and_the_fill_settles(self):
        # Consent for the five rows with room for a sixth; the sixth
        # lands after the click and is moved to the TOP, where sheet
        # order serves it first. Only the id bound keeps it out: FAILS
        # if the walk stops filtering by until_id.
        fill = self._fill(targeted=False, until_id=self._until(), consented=6)
        (late,) = self.lists.add_rows(self.sheet, [{"company": "late.io"}])
        self.lists.move_row(self.sheet, str(late.id), after_id=None)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual(self._runs(fill).filter(row_id=str(late.id)).count(), 0)
        self.assertEqual(self._numbers(self._runs(fill)), [2, 3, 4, 6])
        cursor = cursor_of(str(fill.id))
        self.assertEqual(
            (cursor.after_id, cursor.walked, cursor.offered, cursor.targeted), (str(self.rows[4].id), 5, 4, 4)
        )
        self.assertIsNotNone(cursor.targeted_at)
        # Its runs are open, so the job waits for them: parked, no
        # attempt spent, the fill reading pending.
        self.assertEqual((fill.status, fill.attempts, fill_status(str(fill.id))), (JobStatus.READY, 0, "pending"))
        self.assertIsNotNone(fill.scheduled_at)

    def test_the_walk_follows_sheet_order_and_the_consent_count_caps_it(self):
        # The last row moved to the top: the walk queues it FIRST (the
        # order the user sees), and a consent for 3 rows walks the top
        # three as displayed, not the three oldest.
        self.lists.move_row(self.sheet, str(self.rows[4].id), after_id=None)
        fill = self._fill(targeted=False, until_id=self._until(), consented=3)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        runs = list(self._runs(fill).order_by("id"))
        self.assertEqual(
            [run.row_id for run in runs], [str(self.rows[4].id), str(self.rows[0].id), str(self.rows[1].id)]
        )
        self.assertEqual(self._numbers(runs), [1, 2, 3])
        self.assertEqual((cursor_of(str(fill.id)).walked, cursor_of(str(fill.id)).targeted), (3, 3))

    def test_a_row_moved_out_from_under_the_walk_waits_for_the_next_refill(self):
        # One page walked, then row 5 is moved to the top (above the
        # cursor): the walk never reaches it, exactly as it never
        # reaches a row appended after the click; a row moved the other
        # way would be offered twice and dropped by the open-run key.
        fill = self._fill(targeted=False, until_id=self._until())
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)  # rows 1 and 2
        self.lists.move_row(self.sheet, str(self.rows[4].id), after_id=None)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual(self._numbers(self._runs(fill)), [2, 3, 4])  # row 5 now sits at number 1, unwalked
        self.assertEqual(cursor_of(str(fill.id)).targeted, 3)

    def test_the_cursor_row_moved_below_the_walk_is_offered_twice_and_dropped(self):
        # One page walked, then the cursor row itself (row 2) is moved
        # to the bottom. The cursor's remembered rank is the truth, so
        # the next page starts where row 2 used to be, and row 2 is
        # offered again at its new place: the open-run key drops it.
        fill = self._fill(targeted=False, until_id=self._until())
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)  # rows 1 and 2
        moved = self.rows[1]
        self.lists.move_row(self.sheet, str(moved.id), after_id=str(self.rows[4].id))
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual(self._runs(fill).filter(row_id=str(moved.id)).count(), 1)
        self.assertEqual(self._numbers(self._runs(fill)), [1, 2, 4, 5])  # row 2 now sits at number 5
        self.assertEqual(cursor_of(str(fill.id)).targeted, 4)

    def test_a_deleted_agent_fails_the_walk_on_its_own_verdict(self):
        # One page walked, then the agent is deleted: the next slice
        # fails the job as the kind's verdict (no attempt spent, the
        # why on the job) and sweeps what the first page queued, so
        # nothing is left READY that nothing will run.
        fill = self._fill(targeted=False, until_id=self._until())
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)  # rows 1 and 2 queued
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.READY).count(), 2)
        self.agents.delete(self.agent)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual(
            (fill.status, fill.error_code, fill.error, fill.attempts),
            (JobStatus.FAILED, FillFailureCode.AGENT_MISSING, AGENT_MISSING_MESSAGE, 0),
        )
        self.assertEqual(set(self._runs(fill).values_list("status", flat=True)), {NodeRunStatus.ABANDONED})

    def test_a_walk_whose_list_is_gone_ends(self):
        # A job the list's delete missed: the walk finds no list and the
        # job ends DONE with nothing queued, rather than parking to its
        # attempt cap on a missing row.
        fill = self._fill(targeted=False, until_id=self._until())
        List.objects.filter(id=self.sheet.id).delete()
        self._walk(fill)
        self.assertEqual((fill.status, fill.attempts, self._runs(fill).count()), (JobStatus.DONE, 0, 0))

    def test_a_scoped_fill_stops_at_its_first_n_qualifying_rows(self):
        fill = self._fill(targeted=False, until_id=self._until(), max_row_count=3, consented=3, covered=5)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual(self._numbers(self._runs(fill)), [1, 2, 3])
        self.assertEqual(cursor_of(str(fill.id)).targeted, 3)

    def test_a_fill_that_targets_nothing_completes_on_the_tick_that_ends_its_walk(self):
        self._prompt("Find {{missing}}")
        fill = self._fill(targeted=False, until_id=self._until())
        self._walk(fill)
        self.assertEqual(
            (fill.status, fill_status(str(fill.id)), cursor_of(str(fill.id)).targeted), (JobStatus.DONE, "complete", 0)
        )

    def test_runs_settling_before_the_walk_ends_do_not_complete_it_early(self):
        # No open run is also what a fill looks like between two slices
        # of its walk. FAILS if the poll could run before the target set
        # is whole: the fill would complete, free its cap slot, and
        # strand the runs the next slice lands.
        fill = self._fill(targeted=False, until_id=self._until())
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)  # one page: rows 1 and 2
        self.assertEqual(self._runs(fill).count(), 2)
        NodeRun.objects.filter(fill_run_id=str(fill.id)).update(status=NodeRunStatus.DONE)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)  # the next page, still walking
        self.assertEqual(fill.status, JobStatus.READY)
        self.assertIsNone(cursor_of(str(fill.id)).targeted_at)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)  # the rest of the range: targeted, runs 3 and 5 open
        self.assertEqual((fill.status, self._runs(fill).count()), (JobStatus.READY, 4))
        self.assertIsNotNone(cursor_of(str(fill.id)).targeted_at)

    def test_a_cancel_mid_walk_stops_the_walk_and_leaves_nothing_ready(self):
        fill = self._fill(targeted=False, until_id=self._until())
        # One page, then the user cancels before the next slice.
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill, budget=0)
        self.assertEqual(self._runs(fill).count(), 2)
        self.assertTrue(fill_progress.cancel(str(fill.id)))
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.ABANDONED).count(), 2)
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            self._walk(fill)
        self.assertEqual((fill.status, self._runs(fill).count()), (JobStatus.CANCELLED, 2))
        self.assertIsNone(cursor_of(str(fill.id)).targeted_at)

    def test_a_slice_landing_after_the_cancels_sweep_leaves_its_runs_to_the_judgement(self):
        # The cancel lands after the slice's liveness check read "open"
        # (the sweep finds nothing yet) and the slice inserts anyway:
        # the runs sit READY, unpublished, until the reclaim's orphan
        # judgement abandons them by fill id. No ordering at the stop
        # can prevent this; the judgement is what holds.
        fill = self._fill(targeted=False, until_id=self._until())
        real_is_open = fill_progress.is_open
        calls = {"n": 0}

        def cancel_after_the_check(fill_run_id: str) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                fill_progress.cancel(fill_run_id)
                return True  # the read the slice already took
            return real_is_open(fill_run_id)

        with patch("lists.jobs.fill.fill_progress.is_open", side_effect=cancel_after_the_check):
            self._walk(fill)
        self.assertEqual(fill.status, JobStatus.CANCELLED)
        self.assertEqual(self._runs(fill).filter(status=NodeRunStatus.READY).count(), 4)
        self.assertEqual(NodeRunFlow.abandon_orphans(), 4)
        self.assertEqual(set(self._runs(fill).values_list("status", flat=True)), {NodeRunStatus.ABANDONED})

    def test_the_fill_kind_is_on_the_roster_and_its_payload_round_trips(self):
        fill = self._fill(targeted=False, until_id=self._until(), max_row_count=2, consented=2, covered=5)
        consent = FillJob.model_validate(fill.payload)
        self.assertEqual(
            (consent.mode, consent.max_row_count, consent.consented, consent.column_keys), ("fresh", 2, 2, ["answer"])
        )
        self.assertEqual(consent.scope(fill).fill_run_id, str(fill.id))

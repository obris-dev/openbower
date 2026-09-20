"""The advance: a webhook run is EARNED at the terminal landing, once
every column its wait node waits on is done for the row, and lands
DEFERRED at the next window of the node's cadence. The window helper
is pinned on its boundaries; the advance through land_row on both
lanes (the automatic landing, and a fill-backed settle through the
test helper that restates it).

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, TickReport
from lists.constants import CellSource, NodeRunStatus, StoredCellState, WebhookRunOutcome
from lists.jobs.enqueue_runs import EnqueueRuns
from lists.models import Node, NodeRun
from lists.nodes.registry import WEBHOOK
from lists.processors import UnknownProcessor, processor_for
from lists.processors.column_agent import AIColumnProcessor
from lists.processors.webhook import WebhookProcessor, next_window
from lists.services import cell_truth
from lists.services.columns import ColumnService
from lists.services.fill_admission import FillAdmissionService
from lists.services.fill_processing.landing import LandingContext, land_row
from lists.services.lists import ListService
from lists.services.node_runs import NodeRunFlow
from lists.services.webhook_columns import WebhookColumnService
from lists.services.webhook_runs import WebhookRunResult
from lists.services.workflows import NodeNotFound, WorkflowService
from openbower_schema.fills import CellRunResult
from openbower_schema.lists import ListRowsPage
from webhooks.services import WebhookDestinationService

from .fill_helpers import settle
from .test_fill_worker import quick_config

AGENT = "01AGT" + "A" * 21
OTHER_AGENT = "01AGT" + "B" * 21
ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]
NOW = datetime(2026, 9, 19, 12, 17, 43, tzinfo=UTC)
EARLIER = datetime(2026, 9, 19, 11, 0, tzinfo=UTC)
INTERVAL = 3600


class NextWindowTests(SimpleTestCase):
    def test_a_time_inside_a_window_lands_on_its_next_boundary(self):
        self.assertEqual(next_window(NOW, INTERVAL), datetime(2026, 9, 19, 13, 0, tzinfo=UTC))

    def test_a_time_exactly_on_a_boundary_lands_on_the_one_after(self):
        on_boundary = datetime(2026, 9, 19, 13, 0, tzinfo=UTC)
        self.assertEqual(next_window(on_boundary, INTERVAL), datetime(2026, 9, 19, 14, 0, tzinfo=UTC))

    def test_two_times_in_one_window_agree(self):
        early = datetime(2026, 9, 19, 12, 0, 1, tzinfo=UTC)
        late = datetime(2026, 9, 19, 12, 59, 59, tzinfo=UTC)
        self.assertEqual(next_window(early, INTERVAL), next_window(late, INTERVAL))

    def test_the_smallest_cadence_is_the_next_second(self):
        self.assertEqual(next_window(NOW, 1), datetime(2026, 9, 19, 12, 17, 44, tzinfo=UTC))


class _SheetHarness(TestCase):
    """A sheet with two agent nodes (one filling two columns), one row,
    and a destination; the webhook column is added per test."""

    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.first = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=AGENT)
        self.second = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=OTHER_AGENT)
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(self.first.id)},
            {"key": "score", "label": "Score", "type": "text", "kind": "ai", "node_id": str(self.first.id)},
            {"key": "country", "label": "Country", "type": "text", "kind": "ai", "node_id": str(self.second.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        (self.row,) = self.lists.add_rows(self.sheet, [{"company": "acme.com"}])
        destinations = WebhookDestinationService(account_id=ACCOUNT, user_id=USER)
        self.destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})

    def _add_webhook_column(self, wait_keys: list[str], *, now: datetime = NOW) -> str:
        """The column, then the backfill job it queued, worked to done
        (the runner ticks at `now` so the runs' window is deterministic)."""
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        self.sheet = columns.add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(self.destination.id),
            wait_keys=wait_keys,
            payload_keys=["company"],
            interval_seconds=INTERVAL,
        )
        self._run_jobs(now=now)
        return next(column.node_id for column in self.sheet.columns if column.kind == "webhook")

    @staticmethod
    def _run_jobs(*, now: datetime = NOW) -> TickReport:
        with patch("lists.jobs.enqueue_runs.timezone.now", return_value=now):
            return JobRunner(worker_id="test:1").tick()

    def _land(self, node: Node, cells: dict[str, str], *, now: datetime = NOW, keys: tuple[str, ...] = ()) -> None:
        keys = keys or tuple(
            column.key for column in self.sheet.columns if column.kind == "ai" and column.node_id == str(node.id)
        )
        ctx = LandingContext(
            account_id=ACCOUNT,
            list_id=str(self.sheet.id),
            column_keys=keys,
            fill_run_id=None,
            config_fingerprint="fp",
            node_id=str(node.id),
        )
        run = CellRunResult(cells=cells, declined_cause=StoredCellState.NO_EVIDENCE)
        with patch("lists.services.webhook_runs.timezone.now", return_value=now):
            land_row(ctx, str(self.row.id), run, close=lambda result: True)

    def _webhook_runs(self):
        return NodeRun.objects.filter(kind=WEBHOOK, row_id=str(self.row.id)).order_by("id")


class ProcessorTests(_SheetHarness):
    """The webhook kind's processor: the one place the barrier ahead of
    a webhook node resolves to columns, and the one judgement of which
    rows are owed a run. The factory hands it out by the node's kind."""

    def _processor(self, node_id: str):
        node = self.workflows.get_node(node_id)
        return processor_for(account_id=ACCOUNT, node=node)

    def test_the_factory_answers_by_node_kind_and_refuses_a_kind_without_one(self):
        node_id = self._add_webhook_column(["country"])
        self.assertIsInstance(self._processor(node_id), WebhookProcessor)
        self.assertIsInstance(processor_for(account_id=ACCOUNT, node=self.first), AIColumnProcessor)
        # A kind with no processor (the barrier makes no runs of its
        # own): the factory says so loudly rather than walking nothing.
        node = self.workflows.get_node(node_id)
        barrier = Node.objects.get(path_id=node.path_id, rank=0)
        with self.assertRaises(UnknownProcessor):
            processor_for(account_id=ACCOUNT, node=barrier)

    def test_each_kind_executes_in_exactly_one_shape(self):
        # The webhook kind sends per NODE (one digest for many runs), the
        # agent kind runs per RUN; a dispatcher holding the wrong shape
        # must fail loudly, never silently do nothing. FAILS if a kind
        # gains a silent default for the shape it does not execute in.
        node_id = self._add_webhook_column(["country"])
        flow = NodeRunFlow(worker_id="test:shape")
        with self.assertRaises(NotImplementedError):
            self._processor(node_id).process_run(NodeRun(), flow=flow)
        with self.assertRaises(NotImplementedError):
            processor_for(account_id=ACCOUNT, node=self.first).process_batch(flow=flow, now=datetime.now(UTC))

    def test_wait_keys_are_the_barriers_columns_in_sheet_order(self):
        node_id = self._add_webhook_column(["country", "answer"])
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), ["answer", "score", "country"])

    def test_wait_keys_are_empty_when_the_barrier_or_its_paths_are_gone(self):
        node_id = self._add_webhook_column(["answer"])
        self.sheet.columns = [column for column in self.sheet.columns if column.key not in ("answer", "score")]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), [])
        node = self.workflows.get_node(node_id)
        Node.objects.filter(path_id=node.path_id, rank=0).delete()
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), [])

    def test_enqueue_runs_births_a_deferred_run_at_the_window_for_a_complete_row_only(self):
        node_id = self._add_webhook_column(["country"])
        processor = self._processor(node_id)
        # Never attempted: nothing owed. A retryable state: not complete.
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], now=NOW), 0)
        self._settle(self.row, {"country": StoredCellState.TRANSIENT})
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], now=NOW), 0)
        self._settle(self.row, {"country": StoredCellState.FILLED})
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], now=NOW), 1)
        (run,) = list(self._webhook_runs())
        self.assertEqual(
            (run.status, run.kind, run.node_id, run.row_id), ("deferred", WEBHOOK, node_id, str(self.row.id))
        )
        self.assertEqual((run.not_before, run.position), (next_window(NOW, INTERVAL), 1))
        # Offered again for the same completion: covered, nothing queued.
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], now=NOW), 0)

    def test_wait_ahead_of_is_the_paths_rank_zero_and_refuses_a_node_without_one(self):
        node_id = self._add_webhook_column(["country"])
        node = self.workflows.get_node(node_id)
        self.assertEqual(self.workflows.wait_ahead_of(node).inbound_path_ids, [self.second.path_id])
        with self.assertRaises(NodeNotFound):
            self.workflows.wait_ahead_of(self.first)

    def _settle(self, row, states: dict[str, str], *, at: datetime = EARLIER) -> None:
        with patch("django.utils.timezone.now", return_value=at):
            cell_truth.write(
                account_id=ACCOUNT,
                list_id=str(self.sheet.id),
                row_id=str(row.id),
                fill_run_id=None,
                config_fingerprint="",
                states=states,
                tools={},
                source=CellSource.FILL,
            )


class AdvanceTests(_SheetHarness):
    def test_the_landing_that_completes_the_row_enqueues_one_deferred_run_at_the_window(self):
        webhook_node_id = self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        # `answer` is filled and `score` a settled blank, but `country`
        # has never been attempted: not complete yet.
        self.assertEqual(self._webhook_runs().count(), 0)

        self._land(self.second, {"country": "US"})

        (run,) = list(self._webhook_runs())
        self.assertEqual((run.status, run.kind, run.node_id), (NodeRunStatus.DEFERRED, WEBHOOK, webhook_node_id))
        self.assertEqual((run.list_id, run.position, run.fill_run_id), (str(self.sheet.id), self.row.position, None))
        self.assertEqual(run.not_before, next_window(NOW, INTERVAL))
        self.assertEqual((run.queued_at, run.last_state_change_at), (NOW, NOW))

    def test_a_retryable_failure_on_a_waited_column_is_not_complete(self):
        self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        ctx_run = CellRunResult(cells={}, declined_cause=StoredCellState.TRANSIENT)
        keys = ("country",)
        ctx = LandingContext(ACCOUNT, str(self.sheet.id), keys, None, "fp", str(self.second.id))
        land_row(ctx, str(self.row.id), ctx_run, close=lambda result: True)
        self.assertEqual(self._webhook_runs().count(), 0)

    def test_a_second_completion_while_a_run_is_open_inserts_nothing(self):
        self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        self._land(self.second, {"country": "US"})
        self._land(self.second, {"country": "US"})  # a refill re-landing the same row
        self.assertEqual(self._webhook_runs().count(), 1)

    def test_a_completion_after_a_settled_run_opens_a_new_one(self):
        self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        self._land(self.second, {"country": "US"})
        self._webhook_runs().update(status=NodeRunStatus.DONE)

        later = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)
        self._land(self.second, {"country": "CA"}, now=later)

        first, second = list(self._webhook_runs())
        self.assertEqual((first.status, second.status), (NodeRunStatus.DONE, NodeRunStatus.DEFERRED))
        self.assertEqual(second.not_before, next_window(later, INTERVAL))

    def test_a_sheet_without_a_webhook_column_pays_no_run_insert(self):
        with CaptureQueriesContext(connection) as queries:
            self._land(self.first, {"answer": "yes"})
        inserts = [q["sql"] for q in queries.captured_queries if "INSERT INTO" in q["sql"] and "noderun" in q["sql"]]
        self.assertEqual(inserts, [])
        self.assertEqual(NodeRun.objects.filter(kind=WEBHOOK).count(), 0)

    def test_a_wait_whose_paths_no_longer_resolve_enqueues_nothing_and_raises_nothing(self):
        # The waited-on agent's columns left the sheet while its node
        # lingers: the wait resolves to no column, so there is nothing
        # to judge, and the landing must not decide the row is complete.
        self._add_webhook_column(["answer"])
        self.sheet.columns = [column for column in self.sheet.columns if column.key not in ("answer", "score")]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self._land(self.first, {"answer": "yes"}, keys=("answer", "score"))
        self.assertEqual(self._webhook_runs().count(), 0)

    def test_a_fill_backed_landing_advances_too(self):
        # The manual lane: a fill admitted the real way on a fresh sheet
        # (admission mints the agent's node and its `answer` column),
        # settled through the helper that restates land_row, completes
        # the row for a webhook column waiting on that column.
        sheet = self.lists.create(
            owner_id=USER,
            label="Fresh",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        (row,) = self.lists.add_rows(sheet, [{"company": "example.io"}])
        with patch("lists.services.fill_admission.base.model_for"):
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1
            )
        self._run_jobs()
        WebhookColumnService(account_id=ACCOUNT, user_id=USER).add(
            str(sheet.id),
            label="CRM sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=INTERVAL,
        )

        settle(str(fill.id), str(row.id))

        (run,) = list(NodeRun.objects.filter(kind=WEBHOOK))
        self.assertEqual((run.status, run.row_id, run.list_id), (NodeRunStatus.DEFERRED, str(row.id), str(sheet.id)))


class _BackfilledSheet(_SheetHarness):
    """Three rows with history: rows 1 and 2 complete for (country,
    answer), row 3 only for answer."""

    def setUp(self) -> None:
        super().setUp()
        self.second_row, self.third_row = self.lists.add_rows(
            self.sheet, [{"company": "example.io"}, {"company": "acme.org"}]
        )
        # Rows 1 and 2 complete for (country, answer); row 3 only for answer.
        for row in (self.row, self.second_row, self.third_row):
            self._settle(row, {"answer": StoredCellState.FILLED, "score": StoredCellState.NO_EVIDENCE})
        for row in (self.row, self.second_row):
            self._settle(row, {"country": StoredCellState.FILLED})

    def _settle(self, row, states: dict[str, str], *, at: datetime = EARLIER) -> None:
        # The history predates NOW, the clock the runs are born on: a
        # run always follows the completion it covers.
        with patch("django.utils.timezone.now", return_value=at):
            cell_truth.write(
                account_id=ACCOUNT,
                list_id=str(self.sheet.id),
                row_id=str(row.id),
                fill_run_id=None,
                config_fingerprint="",
                states=states,
                tools={},
                source=CellSource.FILL,
            )

    def _runs(self):
        return NodeRun.objects.filter(kind=WEBHOOK).order_by("position")


class BackfillTests(_BackfilledSheet):
    """Adding a webhook column over a sheet with history queues ONE
    backfill job, whose slices give every row already complete for the
    wait set a run; a config edit that changes the wait SET queues
    another; one that does not, does not."""

    def test_add_queues_one_backfill_job_and_the_request_walks_no_rows(self):
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        with CaptureQueriesContext(connection) as queries:
            self.sheet = columns.add(
                str(self.sheet.id),
                label="CRM sync",
                destination_id=str(self.destination.id),
                wait_keys=["country", "answer"],
                payload_keys=["company"],
                interval_seconds=INTERVAL,
            )
        node_id = next(column.node_id for column in self.sheet.columns if column.kind == "webhook")
        (job,) = list(Job.objects.all())
        self.assertEqual((job.kind, job.status, job.account_id), ("enqueue_runs", JobStatus.READY, ACCOUNT))
        self.assertEqual(job.payload["list_id"], str(self.sheet.id))
        self.assertEqual((job.payload["node_id"], job.payload["scope"]["mode"]), (node_id, "backfill"))
        # The request inserted no run and read no cell state: the walk
        # is the job's.
        self.assertEqual(self._runs().count(), 0)
        touched = [q["sql"] for q in queries.captured_queries if "lists_listcellstate" in q["sql"]]
        self.assertEqual(touched, [])

    def test_the_job_enqueues_a_run_for_every_row_already_complete(self):
        node_id = self._add_webhook_column(["country", "answer"])
        (job,) = list(Job.objects.all())
        self.assertEqual((job.status, job.progress), (JobStatus.DONE, {"after_position": 3, "offered": 2}))
        runs = list(self._runs())
        self.assertEqual([r.row_id for r in runs], [str(self.row.id), str(self.second_row.id)])
        for run in runs:
            self.assertEqual(
                (run.status, run.node_id, run.not_before), (NodeRunStatus.DEFERRED, node_id, next_window(NOW, INTERVAL))
            )
        self.assertEqual([r.position for r in runs], [1, 2])

    def test_the_walk_pages_across_slices_and_a_doubled_slice_adds_nothing(self):
        # One row per page: three slices, each idempotent under the
        # open-run key. Rerunning the finished job's last cursor by hand
        # proves a reclaimed slice cannot double the runs.
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 1):
            self._add_webhook_column(["country", "answer"])
        (job,) = list(Job.objects.all())
        self.assertEqual((job.status, job.progress), (JobStatus.DONE, {"after_position": 3, "offered": 2}))
        self.assertEqual(self._runs().count(), 2)
        kind = EnqueueRuns.model_validate(job.payload)
        with patch("lists.jobs.enqueue_runs.FILL_SCAN_CHUNK", 1):
            cursor = kind.run(job, kind.Progress(after_position=0))
        self.assertEqual(cursor, kind.Progress(after_position=1))
        self.assertEqual(self._runs().count(), 2)

    def test_a_page_re_walked_after_its_runs_were_sent_offers_nothing(self):
        # The open-run key guards only open runs. A slice re-walked after
        # the flush sent its rows (a reclaimed job, a second backfill)
        # must not send the same completion again. FAILS if the
        # processor stops comparing the completion to the newest run.
        self._add_webhook_column(["country", "answer"])
        self.assertEqual(self._runs().count(), 2)
        self._runs().update(status=NodeRunStatus.DONE)
        (job,) = list(Job.objects.all())
        kind = EnqueueRuns.model_validate(job.payload)
        kind.run(job, kind.Progress(after_position=0))
        self.assertEqual(self._runs().count(), 2)
        # A LATER completion of the same row is new work: one new run.
        self._settle(self.row, {"country": StoredCellState.FILLED}, at=datetime(2026, 9, 19, 15, 30, tzinfo=UTC))
        kind.run(job, kind.Progress(after_position=0))
        fresh = list(self._runs().filter(status=NodeRunStatus.DEFERRED))
        self.assertEqual([r.row_id for r in fresh], [str(self.row.id)])

    def test_a_walk_stops_when_the_column_is_gone(self):
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        self.sheet = columns.add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(self.destination.id),
            wait_keys=["country", "answer"],
            payload_keys=["company"],
            interval_seconds=INTERVAL,
        )
        ColumnService(account_id=ACCOUNT, user_id=USER).delete(str(self.sheet.id), key="crm_sync")
        report = self._run_jobs()
        self.assertEqual((report.done, self._runs().count()), (1, 0))

    def test_a_save_that_keeps_the_wait_set_queues_nothing_new(self):
        self._add_webhook_column(["country", "answer"])
        self._runs().update(status=NodeRunStatus.DONE)
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer", "country"],  # the same set, reordered
            payload_keys=["company"],
            interval_seconds=300,
            enabled=False,
        )
        self.assertEqual(Job.objects.count(), 1)
        self._run_jobs()
        self.assertEqual(self._runs().count(), 2)

    def test_a_narrowed_wait_set_backfills_the_rows_complete_under_it(self):
        self._add_webhook_column(["country", "answer"])
        self._runs().update(status=NodeRunStatus.DONE)
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=INTERVAL,
            enabled=True,
        )
        self.assertEqual(Job.objects.filter(status=JobStatus.READY).count(), 1)
        self._run_jobs()
        # All three rows are complete for `answer` alone, but rows 1 and
        # 2 were already SENT for a completion no newer than this one:
        # narrowing the set re-sends nothing the receiver has. Row 3,
        # never sent, gets its first run.
        fresh = list(self._runs().filter(status=NodeRunStatus.DEFERRED))
        self.assertEqual([r.row_id for r in fresh], [str(self.third_row.id)])
        self.assertEqual(self._runs().count(), 3)

    def test_a_widened_wait_set_re_sends_a_row_once_the_added_column_fills(self):
        # Widening adds a column whose later fill moves the completion
        # past the sent run: that IS new work for the receiver.
        self._add_webhook_column(["answer"])
        self._runs().update(status=NodeRunStatus.DONE)
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["answer", "country"],
            payload_keys=["company"],
            interval_seconds=INTERVAL,
            enabled=True,
        )
        self._run_jobs()
        # Rows 1 and 2 completed `country` at EARLIER too, before their
        # runs: nothing new. Row 3 is incomplete under the wider set.
        self.assertEqual(self._runs().filter(status=NodeRunStatus.DEFERRED).count(), 0)
        self._settle(self.row, {"country": StoredCellState.FILLED}, at=datetime(2026, 9, 19, 15, 30, tzinfo=UTC))
        self._run_jobs()  # a second walk would be the advance in practice
        self.assertEqual(self._runs().filter(status=NodeRunStatus.DEFERRED).count(), 0)
        self._land(self.second, {"country": "US"}, now=datetime(2026, 9, 19, 15, 31, tzinfo=UTC))
        fresh = list(self._runs().filter(status=NodeRunStatus.DEFERRED))
        self.assertEqual([r.row_id for r in fresh], [str(self.row.id)])


class CellWordTests(_BackfilledSheet):
    """The cell's word off the row's newest run for its webhook column."""

    def _words(self) -> dict[str, dict[str, str]]:
        rows = [self.row, self.second_row, self.third_row]
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        return {row_id: dict(words) for row_id, words in columns.cell_states_for_rows(self.sheet, rows).items()}

    def test_the_three_words_and_the_absence(self):
        self._add_webhook_column(["country", "answer"])
        first, second = list(self._runs())
        NodeRun.objects.filter(id=first.id).update(
            status=NodeRunStatus.DONE,
            result=WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id="d").model_dump(),
        )
        NodeRun.objects.filter(id=second.id).update(
            status=NodeRunStatus.DONE, result=WebhookRunResult(outcome=WebhookRunOutcome.FAILED, error="x").model_dump()
        )
        self.assertEqual(
            self._words(),
            {str(self.row.id): {"crm_sync": "sent"}, str(self.second_row.id): {"crm_sync": "failed"}},
        )
        # Row 3 was never complete: no run, no word. A run parked mid-
        # retry is open, so it reads waiting.
        NodeRun.objects.filter(id=second.id).update(
            status=NodeRunStatus.DEFERRED,
            result=WebhookRunResult(outcome=WebhookRunOutcome.RETRYING, error="x").model_dump(),
        )
        self.assertEqual(self._words()[str(self.second_row.id)], {"crm_sync": "waiting"})

    def test_the_newest_run_speaks_after_a_re_completion(self):
        self._add_webhook_column(["country", "answer"])
        (first, _second) = list(self._runs())
        NodeRun.objects.filter(id=first.id).update(
            status=NodeRunStatus.DONE,
            result=WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id="d").model_dump(),
        )
        self.assertEqual(self._words()[str(self.row.id)], {"crm_sync": "sent"})
        self._land(self.second, {"country": "CA"}, now=datetime(2026, 9, 19, 15, 30, tzinfo=UTC))
        self.assertEqual(self._words()[str(self.row.id)], {"crm_sync": "waiting"})

    def test_a_run_retired_for_a_missing_row_says_nothing(self):
        self._add_webhook_column(["country", "answer"])
        self._runs().update(status=NodeRunStatus.ROW_MISSING)
        self.assertEqual(self._words(), {})

    def test_a_sheet_without_a_webhook_column_pays_no_query(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self._words(), {})
        self.assertEqual(len(queries.captured_queries), 0)

    def test_the_rows_page_carries_the_words_in_one_query(self):
        self._add_webhook_column(["country", "answer"])
        login_session(self.client)
        url = reverse("lists_rows", kwargs={"id": str(self.sheet.id)})
        with CaptureQueriesContext(connection) as queries:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        page = ListRowsPage(**resp.json())
        self.assertEqual([item.webhooks for item in page.items], [{"crm_sync": "waiting"}, {"crm_sync": "waiting"}, {}])
        run_reads = [q["sql"] for q in queries.captured_queries if "lists_noderun" in q["sql"]]
        self.assertEqual(len(run_reads), 1)


class CustodyTests(_BackfilledSheet):
    def test_deleting_the_webhook_column_deletes_its_runs_with_its_path(self):
        node_id = self._add_webhook_column(["country", "answer"])
        self.assertEqual(self._runs().count(), 2)
        ColumnService(account_id=ACCOUNT, user_id=USER).delete(str(self.sheet.id), key="crm_sync")
        self.assertEqual(NodeRun.objects.filter(node_id=node_id).count(), 0)
        self.assertEqual(Node.objects.filter(id=node_id).count(), 0)

    def test_deleting_the_list_deletes_its_webhook_runs(self):
        self._add_webhook_column(["country", "answer"])
        self.assertEqual(self._runs().count(), 2)
        self.lists.delete(self.sheet)
        self.assertEqual(NodeRun.objects.filter(list_id=str(self.sheet.id)).count(), 0)

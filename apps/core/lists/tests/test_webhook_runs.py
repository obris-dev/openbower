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

from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, TickReport
from lists.cells import LandingContext, RowLanding, WebhookWrite
from lists.constants import CellSource, NodeRunStatus, StoredCellState
from lists.jobs.column_backfill import ColumnBackfillJob
from lists.models import Node, NodeRun
from lists.nodes.entry import Entry
from lists.nodes.registry import COLUMN_AGENT, WEBHOOK
from lists.processors import FillMode, FillScope, UnknownProcessor, processor_for
from lists.processors.column_agent import AIColumnProcessor
from lists.processors.webhook import WebhookProcessor, next_window
from lists.services import cell_truth
from lists.services.columns import ColumnService
from lists.services.fills import FillService
from lists.services.lists import ListService
from lists.services.node_runs import NodeRunFlow
from lists.services.webhook_columns import WebhookColumnService
from lists.services.workflow_reactions import WorkflowReactions
from lists.services.workflows import NodeNotFound, WorkflowService
from openbower_kernel.ranks import key_between
from openbower_schema.fills import CellRunResult
from openbower_schema.lists import ListRowsPage
from webhooks.services import WebhookDestinationService

from .fill_helpers import settle, start_fill
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
        with patch("lists.jobs.column_backfill.timezone.now", return_value=now):
            return JobRunner(worker_id="test:1").tick()

    def _land(self, node: Node, cells: dict[str, str], *, now: datetime = NOW, keys: tuple[str, ...] = ()) -> None:
        keys = keys or tuple(
            column.key for column in self.sheet.columns if column.kind == "ai" and column.node_id == str(node.id)
        )
        run = CellRunResult(cells=cells, declined_cause=StoredCellState.NO_EVIDENCE)
        self._land_run(node, keys, run)
        # The advance is the processors' base's, after the kind's run;
        # the landing itself writes the sheet and the truth only.
        with patch("lists.services.workflow_reactions.timezone.now", return_value=now):
            WorkflowReactions(account_id=ACCOUNT).advance(
                list_id=str(self.sheet.id), row_ids=[str(self.row.id)], from_node_id=str(node.id)
            )

    def _land_run(self, node: Node, keys: tuple[str, ...], run: CellRunResult) -> None:
        """A landing the way the agent processor does it: claim a fresh
        run for the node and row, the kind's writes, land, settle."""
        flow = NodeRunFlow(worker_id="test:land")
        task = NodeRun.objects.create(
            account_id=ACCOUNT,
            node_id=str(node.id),
            kind=COLUMN_AGENT,
            row_id=str(self.row.id),
            list_id=str(self.sheet.id),
            rank=self.row.rank,
            status=NodeRunStatus.READY,
        )
        claimed = flow.claim(str(task.id))
        assert claimed is not None
        writes = processor_for(account_id=ACCOUNT, node=node).on_run_landed(keys, run)
        ctx = LandingContext(list_id=str(self.sheet.id), source=CellSource.NODE, fill_run_id=None)
        with transaction.atomic():
            self.lists.land_row(ctx, RowLanding(str(self.row.id), writes))
            assert flow.settle(str(task.id), result=run.model_dump(), status=NodeRunStatus.DONE)

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
        # A MARKER makes no runs of its own (it heads a path; a reaction
        # skips it and offers the node behind it), so the factory says so
        # loudly rather than walking nothing.
        node = self.workflows.get_node(node_id)
        barrier = self.workflows.nodes_on_path(node.path_id)[0]
        entry = Node(account_id=ACCOUNT, kind=Entry.KIND, rank="a0")
        for marker in (barrier, entry):
            with self.subTest(kind=marker.kind), self.assertRaises(UnknownProcessor):
                processor_for(account_id=ACCOUNT, node=marker)

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
            processor_for(account_id=ACCOUNT, node=self.first).process_batch([], flow=flow, now=datetime.now(UTC))

    def test_the_base_advances_after_a_landed_run_and_after_each_settled_batch_row(self):
        # The advance is the base's, run after the kind's half on BOTH
        # shapes: a run that LANDED on its row is offered downstream; an
        # exited, parked, or retired run is not, on a row or off; a batch
        # advances every (list, row) it settled. FAILS if a kind is
        # left to call the advance itself, or the public pair stops
        # wrapping the private one.
        from lists.processors.base import BatchTally, RunOutcome

        flow = NodeRunFlow(worker_id="test:advance")
        agent = processor_for(account_id=ACCOUNT, node=self.first)
        on_row = NodeRun(account_id=ACCOUNT, list_id="L1", row_id="R1", node_id=str(self.first.id))
        preview = NodeRun(account_id=ACCOUNT, list_id="", row_id=None, node_id=str(self.first.id))
        calls: list[tuple] = []
        spy = patch(
            "lists.services.workflow_reactions.WorkflowReactions.advance",
            side_effect=lambda **kw: calls.append(tuple(sorted(kw.items()))),
        )
        with spy, patch.object(AIColumnProcessor, "_process_run", return_value=RunOutcome.LANDED):
            agent.process_run(on_row, flow=flow)
        for outcome in (RunOutcome.EXITED, RunOutcome.PARKED, RunOutcome.ROW_MISSING):
            with spy, patch.object(AIColumnProcessor, "_process_run", return_value=outcome):
                agent.process_run(on_row, flow=flow)
                agent.process_run(preview, flow=flow)
        self.assertEqual(calls, [(("from_node_id", str(self.first.id)), ("list_id", "L1"), ("row_ids", ["R1"]))])
        # A batch advances ONE page per list, not one call per row.
        node_id = self._add_webhook_column(["country"])
        tally = BatchTally(settled=3, settled_rows=[("L1", "R1"), ("L2", "R9"), ("L1", "R2")])
        calls.clear()
        with (
            patch(
                "lists.services.workflow_reactions.WorkflowReactions.advance",
                side_effect=lambda **kw: calls.append((kw["list_id"], kw["row_ids"])),
            ),
            patch.object(WebhookProcessor, "_process_batch", return_value=tally),
        ):
            self.assertIs(self._processor(node_id).process_batch([], flow=flow, now=NOW), tally)
        self.assertEqual(calls, [("L1", ["R1", "R2"]), ("L2", ["R9"])])

    def test_wait_keys_are_the_barriers_columns_in_sheet_order(self):
        node_id = self._add_webhook_column(["country", "answer"])
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), ["answer", "score", "country"])

    def test_wait_keys_are_empty_when_the_barrier_or_its_paths_are_gone(self):
        node_id = self._add_webhook_column(["answer"])
        columns = list(self.sheet.columns)
        self.sheet.columns = [column for column in columns if column.key not in ("answer", "score")]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), [])
        # The columns back, the barrier gone: each half empties the wait on its own.
        self.sheet.columns = columns
        self.sheet.save(update_fields=["columns", "updated_at"])
        node = self.workflows.get_node(node_id)
        barrier = self.workflows.nodes_on_path(node.path_id)[0]
        barrier.delete()
        self.assertEqual(self._processor(node_id).wait_keys(self.sheet), [])

    def test_enqueue_runs_births_a_deferred_run_at_the_window_for_a_complete_row_only(self):
        node_id = self._add_webhook_column(["country"])
        processor = self._processor(node_id)
        # The occasion is irrelevant to this kind: every mode judges by
        # the barrier alone, so the three are cycled through the calls
        # and the answers must not depend on which one asked. FAILS if
        # the webhook grows a rule for one occasion.
        # The queuing call below runs under the structural mode (the one
        # an agent refuses); the per-mode block at the end queues under
        # every mode, which is what holds a mode rule to red.
        modes = [FillScope(mode=mode) for mode in (FillMode.MANUAL, FillMode.AUTOFILL, FillMode.BACKFILL)]
        # Never attempted: nothing owed. A retryable state: not complete.
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], scope=modes[0], now=NOW), 0)
        self._settle(self.row, {"country": StoredCellState.TRANSIENT})
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], scope=modes[1], now=NOW), 0)
        self._settle(self.row, {"country": StoredCellState.FILLED})
        self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], scope=modes[2], now=NOW), 1)
        (run,) = list(self._webhook_runs())
        self.assertEqual(
            (run.status, run.kind, run.node_id, run.row_id), ("deferred", WEBHOOK, node_id, str(self.row.id))
        )
        self.assertEqual((run.not_before, run.rank), (next_window(NOW, INTERVAL), self.row.rank))
        # Offered again for the same completion, under every mode:
        # covered, nothing queued.
        for scope in modes:
            self.assertEqual(processor.enqueue_runs(self.sheet, [self.row], scope=scope, now=NOW), 0)
        # And every mode QUEUES on a fresh completion, one row each, so
        # a rule returning nothing under any one mode goes red.
        fresh = self.lists.add_rows(self.sheet, [{"company": f"m{n}.io"} for n in range(len(modes))])
        for row, scope in zip(fresh, modes, strict=True):
            self._settle(row, {"country": StoredCellState.FILLED})
            with self.subTest(mode=scope.mode):
                self.assertEqual(processor.enqueue_runs(self.sheet, [row], scope=scope, now=NOW), 1)

    def test_wait_ahead_of_is_the_paths_first_node_and_refuses_a_node_without_one(self):
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
                states=states,
                tools={},
                source=CellSource.NODE,
            )


class AdvanceTests(_SheetHarness):
    def _chained_path(self):
        """A path headed by a wait naming the first agent's path, then an
        agent node, then a webhook node: the general shape."""
        from lists.nodes.column_agent import ColumnAgent
        from lists.nodes.wait_until import WaitUntil
        from lists.nodes.webhook import Webhook

        first_path = self.workflows.get_node(str(self.first.id)).path_id
        _, nodes = self.workflows.create_path(
            self.sheet,
            [
                WaitUntil(inbound_path_ids=[first_path]),
                ColumnAgent(agent_id="01AGENTCCCCCCCCCCCCCCCCCCC"),
                Webhook(destination_id=str(self.destination.id), payload_keys=["company"], interval_seconds=INTERVAL),
            ],
        )
        return nodes

    def _bind(self, node: Node, *, key: str, kind: str) -> None:
        """Give a fixture node the column a real sheet would carry for
        it: a node with no column fills nothing and is offered no rows."""
        column = {"key": key, "label": key.title(), "type": "text", "kind": kind, "node_id": str(node.id)}
        self.sheet.columns = [*self.sheet.columns, column]
        self.sheet.save(update_fields=["columns", "updated_at"])

    def _offers_from(self, node: Node, *, cells: dict[str, str], keys: tuple[str, ...] = ()) -> list[str]:
        """The kinds the landing offered, through the processors' factory."""
        offered: list[str] = []

        def spy(*, account_id, node):
            offered.append(node.kind)
            return processor_for(account_id=account_id, node=node)

        with patch("lists.services.workflow_reactions.processor_for", side_effect=spy):
            self._land(node, cells, keys=keys)
        return offered

    def test_an_arrival_starts_the_entry_paths_and_never_a_node_behind_a_wait(self):
        # The trigger starts at the ENTRY markers. The chained agent sits
        # behind a wait, so an arrival is not its start: its barrier is,
        # through the advance. A marker itself is never offered (it has
        # no processor, so offering it would raise). FAILS if the
        # trigger goes back to "every agent node with a column".
        from lists.nodes.column_agent import ColumnAgent
        from lists.services.workflow_reactions import WorkflowReactions

        _wait, chained, _webhook = self._chained_path()
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "chained", "label": "Chained", "type": "text", "kind": "ai", "node_id": str(chained.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        arrived = self.lists.add_rows(self.sheet, [{"company": "new.io"}])
        offered: list[tuple[str, str]] = []

        def spy(*, account_id, node):
            offered.append((node.kind, str(node.id)))
            return processor_for(account_id=account_id, node=node)

        with patch("lists.services.workflow_reactions.processor_for", side_effect=spy):
            WorkflowReactions(account_id=ACCOUNT).trigger(self.sheet, arrived)
        self.assertEqual(
            sorted(offered), sorted([(ColumnAgent.KIND, str(self.first.id)), (ColumnAgent.KIND, str(self.second.id))])
        )
        self.assertEqual(NodeRun.objects.filter(node_id=str(chained.id)).count(), 0)

    def _marker_behind(self, ahead: Node, behind: Node) -> Node:
        """A marker between two nodes of a path, written past the one
        writer that refuses the shape: what corruption looks like."""
        return Node.objects.create(
            account_id=ACCOUNT,
            workflow_id=ahead.workflow_id,
            path_id=ahead.path_id,
            kind=Entry.KIND,
            identity="",
            config={},
            rank=key_between(ahead.rank, behind.rank),
        )

    def test_a_marker_where_work_belongs_stops_the_advance_and_says_so(self):
        # A marker never runs, so a reaction that reaches one has no
        # rule for it. It stops there (walking past would hand a wait's
        # rows on without its barrier), logs the path, and leaves the
        # run that landed settled DONE. FAILS if the marker reaches the
        # factory, which raises UnknownProcessor into a run that has
        # already done its job.
        _wait, chained, webhook = self._chained_path()
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "chained", "label": "Chained", "type": "text", "kind": "ai", "node_id": str(chained.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self._marker_behind(chained, webhook)
        with self.assertLogs("lists.services.workflow_reactions", level="ERROR") as logs:
            offered = self._offers_from(chained, cells={"chained": "x"})
        self.assertEqual(offered, [])
        self.assertIn(str(chained.path_id), logs.output[0])
        self.assertEqual(NodeRun.objects.filter(node_id=str(webhook.id)).count(), 0)
        settled = NodeRun.objects.filter(node_id=str(chained.id)).values_list("status", flat=True)
        self.assertEqual(list(settled), [NodeRunStatus.DONE])

    def test_a_marker_where_work_belongs_stops_the_trigger_and_says_so(self):
        # The same rule on the other reaction, which finds its node by
        # its own route (walking out from an entry marker) and hands it
        # over with a scope. The corrupt marker is skipped, the agent
        # behind it is still started, and nothing raises. FAILS if the
        # refusal moves out of the one hand-over into the advance alone.
        first = self.workflows.get_node(str(self.first.id))
        entries = list(self.workflows.iter_entry_nodes(first.workflow_id))
        head = next(entry for entry in entries if entry.path_id == first.path_id)
        self._marker_behind(head, first)
        arrived = self.lists.add_rows(self.sheet, [{"company": "new.io"}])
        offered: list[str] = []

        def spy(*, account_id, node):
            offered.append(node.kind)
            return processor_for(account_id=account_id, node=node)

        with (
            self.assertLogs("lists.services.workflow_reactions", level="ERROR"),
            patch("lists.services.workflow_reactions.processor_for", side_effect=spy),
        ):
            WorkflowReactions(account_id=ACCOUNT).trigger(self.sheet, arrived)
        self.assertNotIn(Entry.KIND, offered)
        self.assertEqual(NodeRun.objects.filter(node_id=str(self.first.id), row_id=str(arrived[0].id)).count(), 1)

    def test_rule_two_a_cleared_barrier_offers_the_node_right_after_it_only(self):
        # The first agent is alone on its path, so its landing is the
        # path's end: the wait naming that path is judged for the row
        # (complete: every column the inbound path ends in is done), and
        # the node RIGHT AFTER the wait is offered, not everything
        # behind it, and never the wait itself (it has no processor, so
        # offering it would raise). FAILS if the offer widens to the
        # whole path again, or narrows to one kind.
        from lists.nodes.column_agent import ColumnAgent

        _wait, chained, _webhook = self._chained_path()
        self._bind(chained, key="chained", kind="ai")
        self.assertEqual(self._offers_from(self.first, cells={"answer": "yes", "score": "1"}), [ColumnAgent.KIND])

    def test_rule_two_an_uncleared_barrier_offers_nothing(self):
        # The wait waits on BOTH of the first agent's columns; a landing
        # that writes truth for one only (the other never attempted)
        # leaves the row incomplete, so nothing behind the wait is
        # offered. FAILS if the barrier stops gating.
        self._chained_path()
        self.assertEqual(self._offers_from(self.first, cells={"answer": "yes"}, keys=("answer",)), [])

    def test_rule_one_a_landing_mid_path_offers_the_next_node_on_the_path(self):
        # The chained agent node lands: it is not last on its path, so
        # the next rank key (the webhook node) is offered, and no wait
        # is consulted. FAILS if the advance only ever walks through
        # waits.
        _wait, chained, webhook = self._chained_path()
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "chained", "label": "Chained", "type": "text", "kind": "ai", "node_id": str(chained.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self._bind(webhook, key="crm_sync", kind="webhook")
        self.assertEqual(self._offers_from(chained, cells={"chained": "x"}), [WEBHOOK])

    def test_a_cleared_barrier_starts_the_agent_standing_behind_it(self):
        # The other half of "an arrival never starts a node behind a
        # wait": its barrier does, and a run is what that means. An
        # agent judges by the scope it is handed, so a reaction that
        # hands it none starts nothing at all and the barrier is a dead
        # end. FAILS if the hand-over stops deriving the scope.
        _wait, chained, _webhook = self._chained_path()
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "chained", "label": "Chained", "type": "text", "kind": "ai", "node_id": str(chained.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self._land(self.first, {"answer": "yes", "score": "1"})
        runs = NodeRun.objects.filter(node_id=str(chained.id), row_id=str(self.row.id))
        self.assertEqual([run.status for run in runs], [NodeRunStatus.READY])
        self.assertEqual([run.fill_run_id for run in runs], [None])

    def test_a_cleared_barrier_reads_its_page_of_cells_once_however_many_rows(self):
        # The advance loads rows by id with only their ids and ranks,
        # because the webhook it usually reaches never reads a cell. An
        # agent behind a barrier DOES read every row's cells, and a
        # deferred field loads one instance at a time, so without the
        # agent hydrating its own page that is one query per row. The
        # count must not grow with the page. FAILS if the agent leaves
        # the cells to Django's lazy load.
        _wait, chained, _webhook = self._chained_path()
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "chained", "label": "Chained", "type": "text", "kind": "ai", "node_id": str(chained.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])

        def advance_over(size: int) -> int:
            rows = self.lists.add_rows(self.sheet, [{"company": f"row{size}-{n}.io"} for n in range(size)])
            for row in rows:
                cell_truth.write(
                    account_id=ACCOUNT,
                    list_id=str(self.sheet.id),
                    row_id=str(row.id),
                    fill_run_id=None,
                    states={"answer": StoredCellState.FILLED, "score": StoredCellState.FILLED},
                    tools={},
                    source=CellSource.NODE,
                )
            with CaptureQueriesContext(connection) as captured:
                queued = WorkflowReactions(account_id=ACCOUNT).advance(
                    list_id=str(self.sheet.id), row_ids=[str(row.id) for row in rows], from_node_id=str(self.first.id)
                )
            self.assertEqual(queued, size)
            return len(captured.captured_queries)

        self.assertEqual(advance_over(2), advance_over(6))

    def test_a_page_of_landed_rows_is_one_offer_per_downstream_node(self):
        # Two rows completing in one batch reach the webhook processor
        # as ONE enqueue_runs call carrying both rows, in sheet order,
        # and both get a run. FAILS if the advance loops per row.
        self._add_webhook_column(["country"])
        (second_row,) = self.lists.add_rows(self.sheet, [{"company": "example.io"}])
        for row in (self.row, second_row):
            cell_truth.write(
                account_id=ACCOUNT,
                list_id=str(self.sheet.id),
                row_id=str(row.id),
                fill_run_id=None,
                states={"country": StoredCellState.FILLED},
                tools={},
                source=CellSource.NODE,
            )
        offers: list[list[str]] = []
        real = WebhookProcessor.enqueue_runs

        def spy(processor, target_list, rows, *, scope, now, limit=0):
            offers.append([str(row.id) for row in rows])
            return real(processor, target_list, rows, scope=scope, now=now, limit=limit)

        with patch.object(WebhookProcessor, "enqueue_runs", spy):
            WorkflowReactions(account_id=ACCOUNT).advance(
                list_id=str(self.sheet.id),
                row_ids=[str(second_row.id), str(self.row.id)],
                from_node_id=str(self.second.id),
            )
        self.assertEqual(offers, [[str(self.row.id), str(second_row.id)]])
        self.assertEqual(
            set(NodeRun.objects.filter(kind=WEBHOOK).values_list("row_id", flat=True)),
            {str(self.row.id), str(second_row.id)},
        )

    def test_the_landing_that_completes_the_row_enqueues_one_deferred_run_at_the_window(self):
        webhook_node_id = self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        # `answer` is filled and `score` a settled blank, but `country`
        # has never been attempted: not complete yet.
        self.assertEqual(self._webhook_runs().count(), 0)

        self._land(self.second, {"country": "US"})

        (run,) = list(self._webhook_runs())
        self.assertEqual((run.status, run.kind, run.node_id), (NodeRunStatus.DEFERRED, WEBHOOK, webhook_node_id))
        self.assertEqual((run.list_id, run.rank, run.fill_run_id), (str(self.sheet.id), self.row.rank, None))
        self.assertEqual(run.not_before, next_window(NOW, INTERVAL))
        self.assertEqual((run.queued_at, run.last_state_change_at), (NOW, NOW))

    def test_a_retryable_failure_on_a_waited_column_is_not_complete(self):
        self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        ctx_run = CellRunResult(cells={}, declined_cause=StoredCellState.TRANSIENT)
        self._land_run(self.second, ("country",), ctx_run)
        WorkflowReactions(account_id=ACCOUNT).advance(
            list_id=str(self.sheet.id), row_ids=[str(self.row.id)], from_node_id=str(self.second.id)
        )
        self.assertEqual(self._webhook_runs().count(), 0)

    def test_a_second_completion_while_a_run_is_open_inserts_nothing(self):
        self._add_webhook_column(["country", "answer"])
        self._land(self.first, {"answer": "yes"})
        self._land(self.second, {"country": "US"})
        self._land(self.second, {"country": "US"})  # a second fill re-landing the same row
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
        # The harness claims one agent run for the landing to close; the
        # landing and its advance must insert no run of their own.
        with CaptureQueriesContext(connection) as queries:
            self._land(self.first, {"answer": "yes"})
        inserts = [q["sql"] for q in queries.captured_queries if "INSERT INTO" in q["sql"] and "noderun" in q["sql"]]
        self.assertEqual(len(inserts), 1, inserts)
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
        # The manual lane: a fill started the real way on a fresh sheet
        # (the create mints the agent's node and its `answer` column),
        # settled through the helper that restates land_row, completes
        # the row for a webhook column waiting on that column.
        sheet = self.lists.create(
            owner_id=USER,
            label="Fresh",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        (row,) = self.lists.add_rows(sheet, [{"company": "example.io"}])
        with patch("lists.services.runnable.model_for"):
            _, fill = start_fill(str(sheet.id), account_id=ACCOUNT, user_id=USER, config=quick_config())
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
                states=states,
                tools={},
                source=CellSource.NODE,
            )

    def _runs(self):
        return NodeRun.objects.filter(kind=WEBHOOK).order_by("rank", "id")


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
        self.assertEqual((job.kind, job.status, job.account_id), ("column_backfill", JobStatus.READY, ACCOUNT))
        self.assertEqual(job.payload["list_id"], str(self.sheet.id))
        self.assertEqual(job.payload["node_id"], node_id)
        # The request inserted no run and read no cell state: the walk
        # is the job's.
        self.assertEqual(self._runs().count(), 0)
        touched = [q["sql"] for q in queries.captured_queries if "lists_listcellstate" in q["sql"]]
        self.assertEqual(touched, [])

    def test_the_job_enqueues_a_run_for_every_row_already_complete(self):
        node_id = self._add_webhook_column(["country", "answer"])
        (job,) = list(Job.objects.all())
        self.assertEqual(
            (job.status, job.progress),
            (JobStatus.DONE, {"after_id": str(self.third_row.id), "after_rank": self.third_row.rank}),
        )
        runs = list(self._runs())
        self.assertEqual([r.row_id for r in runs], [str(self.row.id), str(self.second_row.id)])
        for run in runs:
            self.assertEqual(
                (run.status, run.node_id, run.not_before), (NodeRunStatus.DEFERRED, node_id, next_window(NOW, INTERVAL))
            )
        self.assertEqual([r.rank for r in runs], [self.row.rank, self.second_row.rank])

    def test_the_walk_pages_across_slices_and_a_doubled_slice_adds_nothing(self):
        # One row per page: three slices, each idempotent under the
        # open-run key. Rerunning the finished job's last cursor by hand
        # proves a reclaimed slice cannot double the runs.
        with patch("lists.jobs.column_backfill.FILL_SCAN_CHUNK", 1):
            self._add_webhook_column(["country", "answer"])
        (job,) = list(Job.objects.all())
        self.assertEqual(
            (job.status, job.progress),
            (JobStatus.DONE, {"after_id": str(self.third_row.id), "after_rank": self.third_row.rank}),
        )
        self.assertEqual(self._runs().count(), 2)
        kind = ColumnBackfillJob.model_validate(job.payload)
        with patch("lists.jobs.column_backfill.FILL_SCAN_CHUNK", 1):
            cursor = kind.run(job, kind.Progress(after_id=""))
        self.assertEqual(cursor, kind.Progress(after_id=str(self.row.id), after_rank=self.row.rank))
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
        kind = ColumnBackfillJob.model_validate(job.payload)
        kind.run(job, kind.Progress(after_id=""))
        self.assertEqual(self._runs().count(), 2)
        # A LATER completion of the same row is new work: one new run.
        self._settle(self.row, {"country": StoredCellState.FILLED}, at=datetime(2026, 9, 19, 15, 30, tzinfo=UTC))
        kind.run(job, kind.Progress(after_id=""))
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


class CellStateTests(_BackfilledSheet):
    """A Send webhook column's cell speaks off the ONE ledger: SENT or
    FAILED recorded at the send's landing, pending off an open run,
    absence when the row was never due."""

    def _states(self) -> dict[str, dict[str, str]]:
        rows = [self.row, self.second_row, self.third_row]
        states = FillService(account_id=ACCOUNT).cell_states_for_rows(self.sheet, rows)
        return {
            row_id: {key: entry.state for key, entry in entries.items() if key == "crm_sync"}
            for row_id, entries in states.items()
        }

    def test_sent_failed_pending_and_the_absence(self):
        self._add_webhook_column(["country", "answer"])
        first, second = list(self._runs())
        lists = ListService(account_id=ACCOUNT)
        for run, state in ((first, StoredCellState.SENT), (second, StoredCellState.FAILED)):
            lists.land_row(
                LandingContext(list_id=str(self.sheet.id), source=CellSource.NODE, fill_run_id=None),
                RowLanding(run.row_id, [WebhookWrite("crm_sync", state)]),
            )
            NodeRun.objects.filter(id=run.id).update(status=NodeRunStatus.DONE)
        self.assertEqual(
            {row_id: words for row_id, words in self._states().items() if words},
            {str(self.row.id): {"crm_sync": "sent"}, str(self.second_row.id): {"crm_sync": "failed"}},
        )
        # Row 3 was never complete: no run, no record, no word. A run
        # parked mid-retry is open, so its cell reads pending over the
        # failed record beneath it.
        NodeRun.objects.filter(id=second.id).update(status=NodeRunStatus.DEFERRED)
        self.assertEqual(self._states()[str(self.second_row.id)], {"crm_sync": "pending"})

    def test_a_re_completion_reads_pending_over_a_sent_record(self):
        self._add_webhook_column(["country", "answer"])
        (first, _second) = list(self._runs())
        ListService(account_id=ACCOUNT).land_row(
            LandingContext(list_id=str(self.sheet.id), source=CellSource.NODE, fill_run_id=None),
            RowLanding(first.row_id, [WebhookWrite("crm_sync", StoredCellState.SENT)]),
        )
        NodeRun.objects.filter(id=first.id).update(status=NodeRunStatus.DONE)
        self.assertEqual(self._states()[str(self.row.id)], {"crm_sync": "sent"})
        self._land(self.second, {"country": "CA"}, now=datetime(2026, 9, 19, 15, 30, tzinfo=UTC))
        self.assertEqual(self._states()[str(self.row.id)], {"crm_sync": "pending"})

    def test_a_run_retired_for_a_missing_row_says_nothing(self):
        self._add_webhook_column(["country", "answer"])
        self._runs().update(status=NodeRunStatus.ROW_MISSING)
        self.assertEqual({row_id: words for row_id, words in self._states().items() if words}, {})

    def test_the_rows_page_carries_the_states_in_one_ledger_read(self):
        self._add_webhook_column(["country", "answer"])
        login_session(self.client)
        url = reverse("lists_rows", kwargs={"id": str(self.sheet.id)})
        with CaptureQueriesContext(connection) as queries:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        page = ListRowsPage(**resp.json())
        self.assertEqual(
            [item.states.get("crm_sync").state if "crm_sync" in item.states else None for item in page.items],
            ["pending", "pending", None],
        )
        ledger_reads = [q["sql"] for q in queries.captured_queries if "lists_listcellstate" in q["sql"]]
        run_reads = [q["sql"] for q in queries.captured_queries if "lists_noderun" in q["sql"]]
        self.assertEqual((len(ledger_reads), len(run_reads)), (1, 1))


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

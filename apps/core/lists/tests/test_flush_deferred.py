"""The flush: one digest per webhook node per tick over its due runs,
delivered outside any transaction, the runs settled by the answer.
TransactionTestCase so the outside-a-transaction pin is real and the
claim CAS runs against committed rows. The sender is faked at the
destination service's seam; `now` is passed to every tick so the
window is deterministic.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from common.testing import TEST_IDENTITY
from jobs.services import JobRunner
from lists.constants import NODE_RUN_ATTEMPTS, CellSource, NodeRunStatus, StoredCellState, WebhookRunOutcome
from lists.models import Node, NodeRun
from lists.nodes.registry import COLUMN_AGENT, WEBHOOK
from lists.operations.flush_deferred import FlushDeferredOperation
from lists.processors.webhook import COLUMN_REMOVED, DESTINATION_REMOVED, SEND_UNPREPARABLE, next_window
from lists.services import cell_truth
from lists.services.digest_payload import event_id_of
from lists.services.lists import ListService
from lists.services.node_runs import NodeRunFlow
from lists.services.webhook_columns import WebhookColumnService
from lists.services.webhook_runs import WebhookRunResult
from lists.services.workflow_reactions import WorkflowReactions
from lists.services.workflows import WorkflowService
from openbower_schema.webhooks import WebhookEnvelope
from webhooks.constants import DeliveryStatus
from webhooks.delivery.protocol import DeliveryResult
from webhooks.models import WebhookDelivery, WebhookDestination
from webhooks.services import WebhookDestinationService

AGENT = "01AGT" + "A" * 21
OTHER_AGENT = "01AGT" + "B" * 21
ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]
WORKER = "flush-test:1"
INTERVAL = 900
COMPLETED = datetime(2026, 9, 19, 12, 17, 43, tzinfo=UTC)
BOUNDARY = next_window(COMPLETED, INTERVAL)  # 12:30
OK = DeliveryResult(DeliveryStatus.OK, 200, "", 9, "")
TRANSIENT = DeliveryResult(DeliveryStatus.TRANSIENT, 503, "The receiver answered 503.", 9, "")
REJECTED = DeliveryResult(DeliveryStatus.REJECTED, 400, "The receiver answered 400.", 9, "")
BLOCKED = DeliveryResult(DeliveryStatus.BLOCKED, None, "That address is not reachable from here.", 0, "")


class _FakeSender:
    """Answers each send from a script (OK when the script runs out) and
    records what it saw, including whether a transaction was open."""

    def __init__(self, *results: DeliveryResult) -> None:
        self.results = list(results)
        self.calls: list[dict] = []
        self.in_atomic: list[bool] = []

    def send(self, **kwargs) -> DeliveryResult:
        self.calls.append(kwargs)
        self.in_atomic.append(connection.in_atomic_block)
        return self.results.pop(0) if self.results else OK

    def bodies(self) -> list[WebhookEnvelope]:
        return [WebhookEnvelope(**json.loads(call["body"])) for call in self.calls]


class FlushDeferredTests(TransactionTestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.workflows = WorkflowService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        first = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=AGENT)
        second = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=OTHER_AGENT)
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(first.id)},
            {"key": "country", "label": "Country", "type": "text", "kind": "ai", "node_id": str(second.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.rows = self.lists.add_rows(
            self.sheet, [{"company": "acme.com"}, {"company": "example.io"}, {"company": "acme.org"}]
        )
        destinations = WebhookDestinationService(account_id=ACCOUNT, user_id=USER)
        self.destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})

    def _complete(self, row, *, at: datetime = COMPLETED, country: str = StoredCellState.FILLED) -> None:
        # `updated_at` is the base model's auto_now, stamped by the field
        # off django's clock at the write.
        with patch("django.utils.timezone.now", return_value=at):
            cell_truth.write(
                account_id=ACCOUNT,
                list_id=str(self.sheet.id),
                row_id=str(row.id),
                fill_run_id=None,
                states={"answer": StoredCellState.FILLED, "country": country},
                tools={},
                source=CellSource.NODE,
            )

    def _add_column(self, *, now: datetime = COMPLETED, wait_keys=("country", "answer")) -> str:
        """The column, added AFTER the rows completed so the backfill job
        is what seeds the runs, worked at `now` so they land at its
        window."""
        self.sheet = WebhookColumnService(account_id=ACCOUNT, user_id=USER).add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(self.destination.id),
            wait_keys=list(wait_keys),
            payload_keys=["company", "country"],
            interval_seconds=INTERVAL,
        )
        with patch("lists.jobs.enqueue_runs.timezone.now", return_value=now):
            JobRunner(worker_id="jobs-test:1").tick()
        return next(column.node_id for column in self.sheet.columns if column.kind == "webhook")

    def _tick(self, fake: _FakeSender, *, now: datetime = BOUNDARY, worker: str = WORKER):
        with patch("webhooks.services.destinations.WebhookSender", return_value=fake):
            return FlushDeferredOperation(worker_id=worker).run(now=now)

    def _runs(self):
        return NodeRun.objects.filter(kind=WEBHOOK).order_by("rank", "id")

    def test_nothing_is_due_before_the_window(self):
        for row in self.rows:
            self._complete(row)
        self._add_column()
        fake = _FakeSender()
        report = self._tick(fake, now=BOUNDARY - timedelta(seconds=1))
        self.assertEqual((report.nodes, fake.calls), (0, []))
        self.assertEqual({run.status for run in self._runs()}, {NodeRunStatus.DEFERRED})

    def test_the_digest_reads_in_sheet_order_after_a_move(self):
        # The runs are queued in the sheet's order of the time, then the
        # newest row is moved to the top: the digest follows the sheet
        # as it is at delivery, not the order the runs were queued in.
        # FAILS on the runs' own (rank, id) or on id order.
        for row in self.rows:
            self._complete(row)
        self._add_column()
        self.lists.move_row(self.sheet, str(self.rows[2].id), after_id=None)
        fake = _FakeSender()
        self._tick(fake)
        (envelope,) = fake.bodies()
        self.assertEqual(
            [item.row_id for item in envelope.data.items],
            [str(self.rows[2].id), str(self.rows[0].id), str(self.rows[1].id)],
        )

    def test_one_digest_carries_every_due_row_in_sheet_order_and_settles_them_sent(self):
        for row in self.rows:
            self._complete(row)
        node_id = self._add_column()
        fake = _FakeSender()

        report = self._tick(fake)

        self.assertEqual((report.nodes, report.settled, report.parked, report.failed), (1, 3, 0, 0))
        (envelope,) = fake.bodies()
        self.assertEqual((envelope.type, envelope.test), ("digest", False))
        self.assertEqual(envelope.data.sheet.id, str(self.sheet.id))
        self.assertEqual(envelope.data.waited_on, ["answer", "country"])
        items = envelope.data.items
        self.assertEqual([item.row_id for item in items], [str(row.id) for row in self.rows])
        first = items[0]
        self.assertEqual(first.cells, {"company": "acme.com", "country": ""})
        self.assertEqual(first.states, {"answer": "filled", "country": "filled"})
        self.assertEqual(first.completed_at, COMPLETED.isoformat())
        self.assertEqual(
            first.event_id,
            event_id_of(scope=node_id, row_id=str(self.rows[0].id), stamp=COMPLETED.isoformat(), test=False),
        )
        (delivery,) = list(WebhookDelivery.objects.all())
        self.assertEqual((delivery.test, delivery.type, str(delivery.id)), (False, "digest", envelope.id))
        for run in self._runs():
            self.assertEqual((run.status, run.attempts, run.leased_by), (NodeRunStatus.DONE, 1, WORKER))
            self.assertEqual(WebhookRunResult.model_validate(run.result).outcome, WebhookRunOutcome.SENT)
            self.assertEqual(run.result["delivery_id"], str(delivery.id))
        # The POST went out with no transaction open.
        self.assertEqual(fake.in_atomic, [False])

    def test_rows_completing_in_one_window_share_a_digest_and_a_later_one_rides_the_next(self):
        self._complete(self.rows[0])
        self._complete(self.rows[1])
        self._add_column()
        # The third row completes after the boundary: its run lands at
        # the window after.
        later = BOUNDARY + timedelta(minutes=3)
        self._complete(self.rows[2], at=later)
        WorkflowReactions(account_id=ACCOUNT).advance(
            list_id=str(self.sheet.id),
            row_ids=[str(self.rows[2].id)],
            from_node_id=_node_of(self.sheet, "country"),
            now=later,
        )
        fake = _FakeSender()

        self._tick(fake, now=BOUNDARY)
        self._tick(fake, now=next_window(later, INTERVAL))

        first, second = fake.bodies()
        self.assertEqual([item.row_id for item in first.data.items], [str(row.id) for row in self.rows[:2]])
        self.assertEqual([item.row_id for item in second.data.items], [str(self.rows[2].id)])

    def test_the_batch_cap_splits_a_node_across_ticks(self):
        for row in self.rows:
            self._complete(row)
        self._add_column()
        fake = _FakeSender()
        with patch("lists.operations.flush_deferred.DEFERRED_FLUSH_BATCH", 2):
            first = self._tick(fake)
            second = self._tick(fake)
        self.assertEqual((first.settled, second.settled), (2, 1))
        one, two = fake.bodies()
        self.assertEqual(
            ([i.row_id for i in one.data.items], [i.row_id for i in two.data.items]),
            ([str(row.id) for row in self.rows[:2]], [str(self.rows[2].id)]),
        )

    def test_a_paused_column_and_a_disabled_destination_hand_the_batch_back_untouched(self):
        self._complete(self.rows[0])
        node_id = self._add_column()
        fake = _FakeSender()
        columns = WebhookColumnService(account_id=ACCOUNT, user_id=USER)
        columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["country", "answer"],
            payload_keys=["company", "country"],
            interval_seconds=INTERVAL,
            enabled=False,
        )
        report = self._tick(fake)
        self.assertEqual((report.skipped, fake.calls), (1, []))
        # Claimed by the flush and handed back by the kind: still due at
        # its own window, no attempt spent, nobody's.
        run = self._runs().get()
        self.assertEqual((run.status, run.attempts, run.leased_by), (NodeRunStatus.DEFERRED, 0, ""))
        self.assertLessEqual(run.not_before, BOUNDARY)

        columns.update(
            str(self.sheet.id),
            "crm_sync",
            destination_id=str(self.destination.id),
            wait_keys=["country", "answer"],
            payload_keys=["company", "country"],
            interval_seconds=INTERVAL,
            enabled=True,
        )
        WebhookDestination.objects.filter(id=self.destination.id).update(enabled=False)
        report = self._tick(fake)
        self.assertEqual((report.skipped, fake.calls), (1, []))

        WebhookDestination.objects.filter(id=self.destination.id).update(enabled=True)
        report = self._tick(fake)
        self.assertEqual((report.settled, len(fake.calls)), (1, 1))
        self.assertEqual(Node.objects.get(id=node_id).kind, WEBHOOK)

    def test_a_transient_failure_parks_to_the_next_window_and_fails_past_the_cap(self):
        self._complete(self.rows[0])
        self._add_column()
        fake = _FakeSender(TRANSIENT, TRANSIENT)

        report = self._tick(fake)

        self.assertEqual((report.parked, report.failed, report.settled), (1, 0, 0))
        run = self._runs().get()
        self.assertEqual((run.status, run.attempts, run.parked, run.leased_by), (NodeRunStatus.DEFERRED, 1, True, ""))
        self.assertEqual(run.not_before, next_window(BOUNDARY, INTERVAL))
        stored = WebhookRunResult.model_validate(run.result)
        self.assertEqual((stored.outcome, stored.error), (WebhookRunOutcome.RETRYING, TRANSIENT.error))
        self.assertEqual(WebhookDelivery.objects.count(), 1)

        # At the cap the next transient answer is the last: failed, with
        # its own delivery row.
        NodeRun.objects.filter(id=run.id).update(attempts=NODE_RUN_ATTEMPTS)
        report = self._tick(fake, now=run.not_before)
        run.refresh_from_db()
        self.assertEqual((report.failed, run.status, run.attempts), (1, NodeRunStatus.DONE, NODE_RUN_ATTEMPTS + 1))
        stored = WebhookRunResult.model_validate(run.result)
        self.assertEqual((stored.outcome, stored.error), (WebhookRunOutcome.FAILED, TRANSIENT.error))
        self.assertEqual(WebhookDelivery.objects.count(), 2)
        self.assertEqual(stored.delivery_id, str(WebhookDelivery.objects.order_by("-id").first().id))

    def _fails_at_once(self, answer: DeliveryResult) -> None:
        self._complete(self.rows[0])
        self._add_column()
        fake = _FakeSender(answer)
        report = self._tick(fake)
        run = self._runs().get()
        self.assertEqual((report.failed, run.status, run.attempts, len(fake.calls)), (1, NodeRunStatus.DONE, 1, 1))
        stored = WebhookRunResult.model_validate(run.result)
        self.assertEqual((stored.outcome, stored.error), (WebhookRunOutcome.FAILED, answer.error))
        self.assertEqual(stored.delivery_id, str(WebhookDelivery.objects.get().id))

    def test_a_send_lands_sent_on_the_ledger_and_a_rejection_failed(self):
        # The webhook column's cell is on the ONE ledger, written at the
        # send's landing under the node as writer and no fill, before the
        # run closes (the landing's lock order). FAILS if the send stops
        # recording, or records under a fill, or the record follows the
        # run close.
        from lists.models import ListCellState

        self._complete(self.rows[0])
        self._complete(self.rows[1])
        self._add_column()
        fake = _FakeSender()
        # One row per digest, so the second tick's rejection lands on
        # the second row alone.
        with (
            patch("lists.operations.flush_deferred.DEFERRED_FLUSH_BATCH", 1),
            CaptureQueriesContext(connection) as queries,
        ):
            self._tick(fake)
        record = ListCellState.objects.get(row_id=str(self.rows[0].id), column_key="crm_sync")
        self.assertEqual(
            (record.state, record.source, record.fill_run_id), (StoredCellState.SENT, CellSource.NODE, None)
        )
        # The claim is its own statement before the send; the landing is
        # the ledger insert then the close, so the record must precede
        # the LAST noderun update (the settle).
        heads = [q["sql"].lower().split(" where ")[0] for q in queries.captured_queries]
        recorded = next(
            i for i, head in enumerate(heads) if head.startswith("insert") and "lists_listcellstate" in head
        )
        settled = max(i for i, head in enumerate(heads) if head.startswith("update") and "lists_noderun" in head)
        self.assertLess(recorded, settled)

        with patch("lists.operations.flush_deferred.DEFERRED_FLUSH_BATCH", 1):
            self._tick(_FakeSender(REJECTED))
        record = ListCellState.objects.get(row_id=str(self.rows[1].id), column_key="crm_sync")
        self.assertEqual(record.state, StoredCellState.FAILED)

    def test_a_digests_rows_land_on_the_ledger_in_one_statement(self):
        # However many rows a digest carries, their records are ONE
        # upsert, then one settle: a landing is two statements, not two
        # per row. FAILS if the landing records row by row.
        from lists.models import ListCellState

        for row in self.rows:
            self._complete(row)
        self._add_column()
        with CaptureQueriesContext(connection) as queries:
            report = self._tick(_FakeSender())
        self.assertEqual(report.settled, 3)
        heads = [q["sql"].lower().split(" where ")[0] for q in queries.captured_queries]
        inserts = [head for head in heads if head.startswith("insert") and "lists_listcellstate" in head]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(ListCellState.objects.filter(column_key="crm_sync", state=StoredCellState.SENT).count(), 3)

    def test_a_rejected_delivery_fails_the_run_at_once(self):
        self._fails_at_once(REJECTED)

    def test_a_blocked_delivery_fails_the_run_at_once(self):
        self._fails_at_once(BLOCKED)

    def test_a_deleted_row_settles_row_missing_and_leaves_the_digest(self):
        for row in self.rows:
            self._complete(row)
        self._add_column()
        gone = str(self.rows[1].id)
        self.rows[1].delete()
        fake = _FakeSender()

        report = self._tick(fake)

        self.assertEqual(report.settled, 2)
        (envelope,) = fake.bodies()
        self.assertEqual([item.row_id for item in envelope.data.items], [str(self.rows[0].id), str(self.rows[2].id)])
        self.assertEqual(self._runs().get(row_id=gone).status, NodeRunStatus.ROW_MISSING)

    def test_a_deleted_list_settles_list_missing(self):
        self._complete(self.rows[0])
        self._add_column()
        # The List row alone, not the service delete (which purges the
        # runs): the corruption the flush must retire on its own.
        self.sheet.delete()
        fake = _FakeSender()
        self._tick(fake)
        self.assertEqual((self._runs().get().status, fake.calls), (NodeRunStatus.LIST_MISSING, []))

    def test_a_row_no_longer_complete_at_claim_waits_with_its_attempt_handed_back(self):
        self._complete(self.rows[0])
        self._add_column()
        # A refill re-opened the waited-on cell after the advance.
        self._complete(self.rows[0], country=StoredCellState.TRANSIENT)
        fake = _FakeSender()

        report = self._tick(fake)

        run = self._runs().get()
        self.assertEqual((report.parked, fake.calls), (1, []))
        self.assertEqual((run.status, run.attempts, run.parked), (NodeRunStatus.DEFERRED, 0, False))
        self.assertEqual(run.not_before, next_window(BOUNDARY, INTERVAL))

    def test_an_overlapping_tick_claims_nothing_the_first_one_holds(self):
        for row in self.rows:
            self._complete(row)
        node_id = self._add_column()
        first = NodeRunFlow(worker_id="flush-a:1")
        held = first.claim_due_batch(node_id, now=BOUNDARY, limit=10)
        self.assertEqual(len(held), 3)
        fake = _FakeSender()

        report = self._tick(fake, worker="flush-b:2")

        self.assertEqual((report.nodes, report.settled, fake.calls), (0, 0, []))
        result = WebhookRunResult(outcome=WebhookRunOutcome.SENT, delivery_id="x")
        self.assertEqual(
            first.settle_many([str(r.id) for r in held], result.model_dump(), status=NodeRunStatus.DONE), 3
        )

    def test_a_gone_column_fails_the_runs_without_sending(self):
        # The node row alone, not the column delete (which purges the
        # runs): an orphan the flush must close on its own.
        self._complete(self.rows[0])
        node_id = self._add_column()
        fake = _FakeSender()
        Node.objects.filter(id=node_id).delete()
        report = self._tick(fake)
        run = self._runs().get()
        self.assertEqual(
            (report.failed, run.status, run.result["error"], fake.calls), (1, NodeRunStatus.DONE, COLUMN_REMOVED, [])
        )

    def test_a_node_that_crashes_every_tick_is_given_up_once_its_attempts_are_spent(self):
        # A crash reaches the tick's own handler, which parks nothing
        # and settles nothing, so the runs are left PROCESSING and the
        # reclaim returns them DEFERRED with their window untouched:
        # due again, claimed again, crashed again, forever, while the
        # kind's cap sits downstream of the send and never sees them.
        # Under the cap the batch is still held for the reclaim; at it
        # the runs stop. FAILS if the crash path stops giving up.
        self._complete(self.rows[0])
        self._add_column()
        fake = _FakeSender()
        with patch("lists.operations.flush_deferred.processor_for", side_effect=RuntimeError("boom")):
            held = self._tick(fake)
            self.assertEqual((held.nodes, held.failed), (1, 0))
            self.assertEqual(self._runs().get().status, NodeRunStatus.PROCESSING)
            # The laps the reclaim would spend, without waiting them out.
            self._runs().update(status=NodeRunStatus.DEFERRED, attempts=NODE_RUN_ATTEMPTS, leased_by="")
            spent = self._tick(fake)
        run = self._runs().get()
        self.assertEqual(
            (spent.failed, run.status, run.result["outcome"], run.result["error"], fake.calls),
            (1, NodeRunStatus.DONE, WebhookRunOutcome.FAILED, SEND_UNPREPARABLE, []),
        )
        # And the node is gone from the pick: nothing due, no next lap.
        self.assertEqual(self._tick(fake).nodes, 0)

    def test_a_gone_destination_fails_the_runs_without_sending(self):
        self._complete(self.rows[0])
        self._add_column()
        fake = _FakeSender()
        WebhookDestination.objects.filter(id=self.destination.id).delete()
        report = self._tick(fake)
        run = self._runs().get()
        self.assertEqual(
            (report.failed, run.status, run.result["error"], fake.calls),
            (1, NodeRunStatus.DONE, DESTINATION_REMOVED, []),
        )

    def test_the_agent_lane_runs_of_the_same_rows_are_never_claimed(self):
        self._complete(self.rows[0])
        self._add_column()
        agent_run = NodeRun.objects.create(
            account_id=ACCOUNT,
            fill_run_id=None,
            node_id=_node_of(self.sheet, "country"),
            kind=COLUMN_AGENT,
            row_id=str(self.rows[0].id),
            list_id=str(self.sheet.id),
            rank=self.rows[0].rank,
            status=NodeRunStatus.READY,
            not_before=BOUNDARY - timedelta(hours=1),
            last_state_change_at=BOUNDARY,
        )
        self._tick(_FakeSender())
        agent_run.refresh_from_db()
        self.assertEqual((agent_run.status, agent_run.attempts), (NodeRunStatus.READY, 0))

    def test_the_command_runs_a_tick_and_logs_the_report(self):
        self._complete(self.rows[0])
        self._add_column(now=BOUNDARY - timedelta(hours=2))
        with (
            patch("webhooks.services.destinations.WebhookSender", return_value=_FakeSender()),
            self.assertLogs("lists.management.commands.flush_deferred", level="INFO") as logs,
        ):
            call_command("flush_deferred")
        self.assertIn("settled=1", logs.output[0])
        self.assertEqual(self._runs().get().status, NodeRunStatus.DONE)


def _node_of(sheet, key: str) -> str:
    return next(column.node_id for column in sheet.columns if column.key == key)

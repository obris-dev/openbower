"""The node-run state machine's provisioning half: a pushed row enqueues
null-run NodeRuns READY (via handle_ingest_event, inside the apply
transaction), the provisioner publishes each to the bus and marks it
QUEUED, and the reclaim scan reclaims a consumer that died mid-run. The broker
is mocked at its client boundary (confluent_kafka.Producer); everything
else (lists, admission, the task rows) is real.

The claim/run/land half is in test_consume_node_runs.py, which reuses
this module's AutofillHarness.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_node_runs
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.db import IntegrityError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from openbower_schema.lists import AiColumn

from ..constants import NodeRunStatus
from ..ingest.consumer import handle_ingest_event
from ..ingest.events import IngestEvent
from ..models import ListRow, NodeRun
from ..nodes.registry import COLUMN_AGENT, WEBHOOK
from ..operations.provision import AutofillProvisionOperation
from ..services import autofill
from ..services.fill_admission import FillAdmissionService
from ..services.lists import ListService
from ..services.node_runs import PROCESSING_STALE_SECONDS, NodeRunFlow
from ..services.workflows import WorkflowService
from .test_fill_worker import quick_config

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"
AGENT_A = "01AGT" + "A" * 21
AGENT_B = "01AGT" + "B" * 21


class AutofillHarness(TransactionTestCase):
    """Shared setup for the autofill path: TransactionTestCase so a
    worker/consumer opening its own connection (and admission's
    select_for_update) never deadlocks against a test-wrapping
    transaction."""

    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)

    def _ai_sheet(self, *, rows: int = 2):
        """A sheet with one AI column bound to an agent, built the real
        way: ListService.create + admission (which mints the ephemeral
        agent, its node, and stamps the column's `fill.node_id`).
        Returns the sheet, the node id the processor will resolve, and
        the fill."""
        sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(sheet, [{"company": f"seed{n}.com"} for n in range(rows)])
        with patch("lists.services.fill_admission.base.model_for"):
            fill = FillAdmissionService(account_id=ACCOUNT, user_id=USER).admit(
                list_id=str(sheet.id), config=quick_config(), confirmed_row_count=rows
            )
        sheet.refresh_from_db()
        node_id = next(column.node_id for column in sheet.columns if isinstance(column, AiColumn))
        return sheet, node_id, fill

    def _plain_sheet(self):
        return self.lists.create(
            owner_id=USER,
            label="Plain",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )

    def _push(self, sheet, rows: list[dict], *, account_id: str = ACCOUNT, list_id: str | None = None) -> str:
        event = IngestEvent(
            event_id=f"evt-{ListRow.objects.count()}-{id(rows)}",
            list_id=list_id or str(sheet.id),
            account_id=account_id,
            user_id=USER,
            rows=rows,
            received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        )
        return handle_ingest_event(event)

    def _null_run_tasks(self):
        return NodeRun.objects.filter(fill_run_id__isnull=True)

    def _new_row_ids(self, sheet, before: set[str]) -> set[str]:
        return {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))} - before


class EnqueueTests(AutofillHarness):
    def test_a_push_to_an_ai_sheet_enqueues_one_ready_task_per_row_and_node(self) -> None:
        sheet, node_id, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}

        self.assertEqual(self._push(sheet, [{"company": "newco.com"}, {"company": "newco2.io"}]), "applied")

        new_ids = self._new_row_ids(sheet, before)
        tasks = list(self._null_run_tasks())
        # One task per (pushed row, distinct node among the AI columns):
        # two rows, one node => exactly two null-run tasks, born READY.
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t.row_id for t in tasks}, new_ids)
        for task in tasks:
            self.assertIsNone(task.fill_run_id)
            self.assertEqual(task.node_id, node_id)
            self.assertEqual(task.kind, COLUMN_AGENT)
            self.assertEqual(task.account_id, ACCOUNT)
            # Denormalized from the target sheet: an autofill run has no
            # Fill, so its list comes off the List it was pushed to.
            self.assertEqual(task.list_id, str(sheet.id))
            self.assertEqual(task.status, NodeRunStatus.READY)
            self.assertIsNotNone(task.last_state_change_at)

    def test_a_push_that_overrides_an_ai_column_skips_that_node(self) -> None:
        sheet, node_id, _ = self._ai_sheet()
        ai_key = next(c.key for c in sheet.columns if isinstance(c, AiColumn))
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}

        # One row pins the AI column (an override), one leaves it blank.
        self.assertEqual(
            self._push(sheet, [{"company": "override.com", ai_key: "PINNED"}, {"company": "blank.com"}]),
            "applied",
        )

        tasks = list(self._null_run_tasks())
        # Only the row that left the AI column blank is owed a fill; the
        # overridden node gets no task.
        self.assertEqual(len(tasks), 1)
        (task,) = tasks
        blank_row = ListRow.objects.get(list_id=str(sheet.id), data__company="blank.com")
        self.assertEqual(task.row_id, str(blank_row.id))
        self.assertEqual(task.node_id, node_id)

        # The pushed value persisted as the producer sent it.
        override_row = ListRow.objects.get(list_id=str(sheet.id), data__company="override.com")
        self.assertEqual(override_row.data[ai_key], "PINNED")
        self.assertEqual(self._new_row_ids(sheet, before), {str(blank_row.id), str(override_row.id)})

    def test_a_multi_column_agent_skips_only_when_every_column_is_filled(self) -> None:
        # One node (an agent) owns TWO columns. A row that fills BOTH is skipped (no
        # work left); a row that leaves one blank STILL enqueues. This pins
        # the all() semantics a single-column fixture cannot (there
        # all([x]) == any([x]) == x). A "0" counts as filled (it strips
        # truthy), never blank.
        sheet = self.lists.create(
            owner_id=USER,
            label="Multi",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        # A real node: the processor that judges the rows is the NODE's.
        node_id = str(WorkflowService(account_id=ACCOUNT).get_or_create_column_agent_node(sheet, agent_id=AGENT_A).id)
        sheet.columns = [
            *sheet.columns,
            {"key": "a", "label": "A", "type": "text", "kind": "ai", "node_id": node_id},
            {"key": "b", "label": "B", "type": "text", "kind": "ai", "node_id": node_id},
        ]
        sheet.save(update_fields=["columns", "updated_at"])
        full, partial = self.lists.add_rows(
            sheet,
            [
                {"company": "full.co", "a": "0", "b": "y"},  # both of the node's columns filled ("0" counts) -> skip
                {"company": "partial.co", "a": "x"},  # b blank -> still owes the node
            ],
        )
        sheet.refresh_from_db()
        created = autofill.enqueue_rows(account_id=ACCOUNT, target_list=sheet, rows=[full, partial])
        self.assertEqual(created, 1)  # only the partial row's node has work
        self.assertEqual(self._null_run_tasks().filter(row_id=str(partial.id)).count(), 1)
        self.assertEqual(self._null_run_tasks().filter(row_id=str(full.id)).count(), 0)

    def test_a_push_to_a_two_node_sheet_enqueues_one_task_per_node_for_the_row(self) -> None:
        # Two distinct nodes on one sheet: the one shape where a row owes
        # more than one task, and the only one the task-id message key
        # touches. Both land under the (row, node) key.
        sheet = self.lists.create(
            owner_id=USER,
            label="Two nodes",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        workflows = WorkflowService(account_id=ACCOUNT)
        node_a = str(workflows.get_or_create_column_agent_node(sheet, agent_id=AGENT_A).id)
        node_b = str(workflows.get_or_create_column_agent_node(sheet, agent_id=AGENT_B).id)
        sheet.columns = [
            *sheet.columns,
            {"key": "a", "label": "A", "type": "text", "kind": "ai", "node_id": node_a},
            {"key": "b", "label": "B", "type": "text", "kind": "ai", "node_id": node_b},
        ]
        sheet.save(update_fields=["columns", "updated_at"])
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self.assertEqual(self._push(sheet, [{"company": "both.co"}]), "applied")
        [row_id] = self._new_row_ids(sheet, before)
        self.assertEqual({t.node_id for t in self._null_run_tasks().filter(row_id=row_id)}, {node_a, node_b})

    def test_a_push_to_a_sheet_with_no_ai_columns_enqueues_nothing(self) -> None:
        plain = self._plain_sheet()
        self.assertEqual(self._push(plain, [{"company": "newco.com"}]), "applied")
        self.assertEqual(self._null_run_tasks().count(), 0)

    def test_re_enqueue_of_the_same_row_and_node_is_a_no_op(self) -> None:
        sheet, _, _ = self._ai_sheet()
        created = self.lists.add_rows(sheet, [{"company": "dupe.com"}])
        sheet.refresh_from_db()

        first = autofill.enqueue_rows(account_id=ACCOUNT, target_list=sheet, rows=created)
        self.assertEqual(first, 1)
        self.assertEqual(self._null_run_tasks().filter(row_id=str(created[0].id)).count(), 1)

        # The partial unique (row_id, node_id) WHERE fill_run_id IS NULL
        # makes a second enqueue a no-op (bulk_create ignore_conflicts).
        autofill.enqueue_rows(account_id=ACCOUNT, target_list=sheet, rows=created)
        self.assertEqual(self._null_run_tasks().filter(row_id=str(created[0].id)).count(), 1)


@override_settings(INGEST_KAFKA_BOOTSTRAP_SERVERS="kafka:9092")
class ProvisionerTests(AutofillHarness):
    def _run_provisioner(self, producer):
        with patch("confluent_kafka.Producer", return_value=producer):
            AutofillProvisionOperation(worker_id="test:prov", stop=threading.Event()).run(once=True)

    def test_it_publishes_each_ready_task_and_marks_it_queued(self) -> None:
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "target.co"}])
        [row_id] = self._new_row_ids(sheet, before)
        task = self._null_run_tasks().get(row_id=row_id)
        self.assertEqual(task.status, NodeRunStatus.READY)

        producer = MagicMock()
        producer.flush.return_value = 0  # the broker acked
        self._run_provisioner(producer)

        # Published: task id in the value, keyed by task id, on the topic.
        producer.produce.assert_called_once()
        args, kwargs = producer.produce.call_args
        self.assertEqual(args[0], "list.fill.autofill")
        self.assertEqual(kwargs["key"], str(task.id).encode())
        self.assertEqual(json.loads(kwargs["value"])["task_id"], str(task.id))
        producer.flush.assert_called_once()

        # And only THEN marked QUEUED.
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.QUEUED)
        self.assertIsNotNone(task.queued_at)

    def test_a_failed_publish_backs_off_and_leaves_the_task_ready(self) -> None:
        # Publish-first-then-mark: if the ack never lands, the task must
        # stay READY so the next pass re-picks it (never a QUEUED task no
        # message names). A broker blip backs off rather than crashing the
        # provisioner (resilient like the DatabaseError branch).
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "target.co"}])
        [row_id] = self._new_row_ids(sheet, before)
        task = self._null_run_tasks().get(row_id=row_id)

        producer = MagicMock()
        producer.flush.return_value = 1  # the ack never arrived
        self._run_provisioner(producer)  # backs off, does not raise

        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.READY)
        self.assertIsNone(task.queued_at)

    def test_mark_queued_no_ops_when_the_task_moved_since_the_page_read(self) -> None:
        # The park race: the provisioner reads a READY task into its page
        # and publishes it, but before the mark runs a consumer claims it
        # and PARKS it (a fast retriable blank). The park returns it to
        # READY, bumps last_state_change_at, AND consumes its message.
        # Marking it QUEUED now would strand it (no message; reclaim skips
        # QUEUED; the provisioner re-picks only READY). The token guard
        # makes the stale mark a no-op, leaving it READY for the next pass.
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "target.co"}])
        [row_id] = self._new_row_ids(sheet, before)
        page_task = self._null_run_tasks().get(row_id=row_id)  # the object the provisioner holds

        # A consumer claims then parks it; the real transitions bump the token.
        flow = NodeRunFlow(worker_id="test:consumer")
        self.assertIsNotNone(flow.claim(str(page_task.id)))
        self.assertTrue(flow.park(str(page_task.id), backoff_seconds=60, result={}))
        parked = self._null_run_tasks().get(id=page_task.id)
        self.assertEqual(parked.status, NodeRunStatus.READY)
        self.assertGreater(parked.last_state_change_at, page_task.last_state_change_at)  # transition re-stamped

        # The provisioner's stale mark carries the old token: it must not fire.
        self.assertFalse(NodeRunFlow.mark_queued(page_task))
        parked.refresh_from_db()
        self.assertEqual(parked.status, NodeRunStatus.READY)
        self.assertIsNone(parked.queued_at)

    def test_settle_restamps_last_state_change_at(self) -> None:
        # Every transition bumps last_state_change_at (the reclaim cursor
        # and audit). Prove SETTLE does: a task claimed long ago and
        # settled now must read fresh from its settle, not its claim, or
        # the reclaim cursor and freshness reads would lag reality.
        sheet, _, _ = self._ai_sheet()
        before = {str(r.id) for r in ListRow.objects.filter(list_id=str(sheet.id))}
        self._push(sheet, [{"company": "settle.co"}])
        [row_id] = self._new_row_ids(sheet, before)
        task = self._null_run_tasks().get(row_id=row_id)

        flow = NodeRunFlow(worker_id="test:consumer")
        self.assertIsNotNone(flow.claim(str(task.id)))
        # Age the claim stamp so the settle's re-stamp is unambiguous.
        aged = timezone.now() - timedelta(seconds=300)
        NodeRun.objects.filter(id=task.id).update(last_state_change_at=aged)
        self.assertTrue(flow.settle(str(task.id), {}, status=NodeRunStatus.DONE))
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.DONE)
        self.assertGreater(task.last_state_change_at, aged)  # settle re-stamped, not left at the aged claim


class ReaperTests(AutofillHarness):
    def test_it_reclaims_a_stale_processing_task_and_leaves_a_fresh_one(self) -> None:
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "a.co"}, {"company": "b.co"}])
        stale, fresh = list(self._null_run_tasks().order_by("id"))

        now = timezone.now()
        # Both PROCESSING under a (now dead or live) consumer; only the
        # stale one is past the window.
        NodeRun.objects.filter(id=stale.id).update(
            status=NodeRunStatus.PROCESSING,
            leased_by="dead:1",
            last_state_change_at=now - timedelta(seconds=PROCESSING_STALE_SECONDS + 60),
        )
        NodeRun.objects.filter(id=fresh.id).update(
            status=NodeRunStatus.PROCESSING,
            leased_by="live:2",
            last_state_change_at=now,
        )

        self.assertEqual(NodeRunFlow.reclaim_stale_processing(), 1)

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.status, NodeRunStatus.READY)
        self.assertEqual(stale.leased_by, "")
        self.assertIsNone(stale.processing_at)
        # The fresh one is a live run, untouched.
        self.assertEqual(fresh.status, NodeRunStatus.PROCESSING)
        self.assertEqual(fresh.leased_by, "live:2")

    def test_reclaim_never_touches_a_stale_queued_task(self) -> None:
        # Reclaim is PROCESSING-only. A QUEUED task (published, a durable
        # message still names it) is NOT reclaimed even when aged far past
        # the window: re-handing it would duplicate present work, and the
        # consumer's claim CAS already drops a duplicate delivery. FAILS if
        # the scan ever widens to QUEUED or to an age-only filter.
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "queued.co"}])
        (task,) = list(self._null_run_tasks())
        NodeRun.objects.filter(id=task.id).update(
            status=NodeRunStatus.QUEUED,
            last_state_change_at=timezone.now() - timedelta(seconds=PROCESSING_STALE_SECONDS + 600),
        )
        self.assertEqual(NodeRunFlow.reclaim_stale_processing(), 0)  # nothing reclaimed
        task.refresh_from_db()
        self.assertEqual(task.status, NodeRunStatus.QUEUED)  # still QUEUED, untouched

    def test_a_stale_webhook_run_returns_to_deferred_never_ready(self) -> None:
        # A flush that died mid-batch leaves its webhook runs PROCESSING.
        # READY would hand them to the agent worker, which cannot run a
        # webhook; DEFERRED puts them back where the next flush tick
        # finds them, at the window that was already due. FAILS if the
        # reclaim stops branching on kind.
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "a.co"}])
        (agent_run,) = list(self._null_run_tasks())
        now = timezone.now()
        window = now - timedelta(seconds=120)
        stale_at = now - timedelta(seconds=PROCESSING_STALE_SECONDS + 60)
        webhook_run = _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.PROCESSING, not_before=window)
        NodeRun.objects.filter(id__in=[agent_run.id, webhook_run.id]).update(
            status=NodeRunStatus.PROCESSING, leased_by="dead:1", last_state_change_at=stale_at
        )

        self.assertEqual(NodeRunFlow.reclaim_stale_processing(), 2)

        agent_run.refresh_from_db()
        webhook_run.refresh_from_db()
        self.assertEqual(agent_run.status, NodeRunStatus.READY)
        self.assertEqual((webhook_run.status, webhook_run.leased_by, webhook_run.processing_at), ("deferred", "", None))
        self.assertEqual(webhook_run.not_before, window)

    def test_the_agent_pick_never_sees_a_webhook_run(self) -> None:
        # The lane gate the provisioner relies on: a due webhook run in
        # any status the agent lane could otherwise read is invisible to
        # its pick. FAILS if the pick drops its kind filter AND a webhook
        # run ever reaches READY.
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "a.co"}])
        (agent_run,) = list(self._null_run_tasks())
        _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.READY, not_before=None)
        picked = list(NodeRunFlow.iter_ready(limit=10))
        self.assertEqual([t.id for t in picked], [agent_run.id])


def _webhook_run(sheet, row_id: str, *, status: NodeRunStatus, not_before) -> NodeRun:
    return NodeRun.objects.create(
        account_id=ACCOUNT,
        fill_run_id=None,
        node_id="01NODEWEBHOOK" + "0" * 13,
        kind=WEBHOOK,
        row_id=row_id,
        list_id=str(sheet.id),
        position=1,
        status=status,
        not_before=not_before,
        last_state_change_at=timezone.now(),
    )


class OpenRunKeyTests(AutofillHarness):
    """The automatic lane's idempotency key: one OPEN run per (row,
    node), settled runs being history."""

    def test_a_second_open_run_for_the_row_and_node_is_refused(self) -> None:
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "a.co"}])
        (agent_run,) = list(self._null_run_tasks())
        _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.DEFERRED, not_before=None)
        with self.assertRaises(IntegrityError):
            _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.DEFERRED, not_before=None)

    def test_a_settled_run_lets_the_row_and_node_open_a_new_one(self) -> None:
        sheet, _, _ = self._ai_sheet()
        self._push(sheet, [{"company": "a.co"}])
        (agent_run,) = list(self._null_run_tasks())
        first = _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.DONE, not_before=None)
        second = _webhook_run(sheet, agent_run.row_id, status=NodeRunStatus.DEFERRED, not_before=None)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(NodeRun.objects.filter(kind=WEBHOOK, row_id=agent_run.row_id).count(), 2)

    def test_a_run_with_no_kind_is_refused_at_the_insert(self) -> None:
        # The kind is a lane, and a CharField silently stores "" when a
        # writer forgets it; the check constraint makes that an error at
        # the insert instead of a run no lane will ever claim.
        with self.assertRaises(IntegrityError):
            NodeRun.objects.create(
                account_id=ACCOUNT,
                fill_run_id=None,
                node_id="01NODEKINDLESS" + "0" * 12,
                row_id="01ROW" + "0" * 21,
                list_id="01LIST" + "0" * 20,
                status=NodeRunStatus.READY,
                last_state_change_at=timezone.now(),
            )

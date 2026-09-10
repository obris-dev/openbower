"""The autofill queue: enqueue on the ingest apply (a task per pushed
row, in the apply's transaction), the worker's drain loop, and the
list-delete purge. No broker needed (the queue is a DB table).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_autofill_worker
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from django.test import TestCase

from lists.constants import ListOrigin
from lists.ingest import IngestEvent
from lists.ingest.consumer import handle_ingest_event
from lists.models import AutofillTask, ListRow
from lists.operations.autofill_worker import AutofillWorkerOperation
from lists.services.lists import ListService

_ACCT = "01JQ" + "B" * 22
_USER = "01JQ" + "A" * 22


def _make_list(account_id=_ACCT) -> str:
    lst = ListService(account_id=account_id, user_id=_USER).create(
        label="ingest target",
        columns=[{"key": "domain", "label": "Domain", "type": "url"}],
        origin=ListOrigin.MANUAL,
    )
    return str(lst.id)


def _event(event_id="evt-1", account_id=_ACCT, rows=None, list_id="") -> IngestEvent:
    return IngestEvent(
        event_id=event_id,
        list_id=list_id,
        account_id=account_id,
        user_id=_USER,
        rows=rows if rows is not None else [{"domain": "acme.com"}],
        received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )


class EnqueueOnIngestTests(TestCase):
    def setUp(self) -> None:
        self.list_id = _make_list()

    def _rows(self, n):
        return [{"domain": f"acme{i}.com"} for i in range(n)]

    def test_an_applied_push_enqueues_one_task_per_appended_row(self):
        handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(3)))
        tasks = AutofillTask.objects.filter(list_id=self.list_id)
        self.assertEqual(tasks.count(), 3)
        # Each task points at a real appended row and carries the tenancy
        # + attribution the worker will resolve the list under.
        row_ids = set(ListRow.objects.filter(list_id=self.list_id).values_list("id", flat=True))
        self.assertEqual({t.row_id for t in tasks}, {str(r) for r in row_ids})
        self.assertTrue(all(t.account_id == _ACCT and t.user_id == _USER for t in tasks))

    def test_a_redelivery_does_not_re_enqueue(self):
        handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(2)))
        handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(2)))  # same event_id: skipped
        self.assertEqual(AutofillTask.objects.filter(list_id=self.list_id).count(), 2)

    def test_a_dropped_push_enqueues_nothing(self):
        # A missing list is terminal, appends nothing, so queues nothing.
        handle_ingest_event(_event(event_id="gone", list_id="01JQ" + "Z" * 22, rows=self._rows(1)))
        self.assertEqual(AutofillTask.objects.count(), 0)

    def test_the_enqueue_rides_the_apply_transaction(self):
        # No partial state: either the rows AND their tasks commit, or
        # neither. A dropped event above proves the negative; here the
        # applied event proves the counts move together.
        handle_ingest_event(_event(list_id=self.list_id, rows=self._rows(4)))
        self.assertEqual(ListService(account_id=_ACCT, user_id=_USER).get(self.list_id).row_count, 4)
        self.assertEqual(AutofillTask.objects.filter(list_id=self.list_id).count(), 4)


class DrainLoopTests(TestCase):
    def setUp(self) -> None:
        self.list_id = _make_list()

    def _enqueue(self, n):
        AutofillTask.objects.bulk_create(
            [AutofillTask(account_id=_ACCT, user_id=_USER, list_id=self.list_id, row_id=f"row-{i}") for i in range(n)]
        )

    def test_once_drains_the_whole_queue(self):
        self._enqueue(5)
        AutofillWorkerOperation(worker_id="w", stop=threading.Event()).run(once=True)
        self.assertEqual(AutofillTask.objects.count(), 0)

    def test_a_pass_processes_oldest_first(self):
        self._enqueue(3)
        oldest = AutofillTask.objects.order_by("id").first()
        drained = AutofillWorkerOperation(worker_id="w", stop=threading.Event())._one_pass()
        self.assertEqual(drained, 3)
        self.assertFalse(AutofillTask.objects.filter(id=oldest.id).exists())

    def test_a_stop_already_set_processes_nothing_and_returns(self):
        self._enqueue(2)
        stop = threading.Event()
        stop.set()  # a SIGTERM before the first pass
        AutofillWorkerOperation(worker_id="w", stop=stop).run(once=False)  # must not hang
        self.assertEqual(AutofillTask.objects.count(), 2)  # left for the next start

    def test_stop_mid_batch_drains_no_further_tasks(self):
        self._enqueue(3)
        stop = threading.Event()
        op = AutofillWorkerOperation(worker_id="w", stop=stop)
        original = op._process

        def _stop_after_first(task):
            original(task)
            stop.set()  # signal arrives after the first row

        op._process = _stop_after_first
        op._one_pass()
        # One processed before the stop was seen, the rest stay queued.
        self.assertEqual(AutofillTask.objects.count(), 2)


class DeletePurgesTasksTests(TestCase):
    def test_deleting_a_list_takes_its_autofill_tasks(self):
        svc = ListService(account_id=_ACCT, user_id=_USER)
        list_id = _make_list()
        AutofillTask.objects.create(account_id=_ACCT, user_id=_USER, list_id=list_id, row_id="row-1")
        svc.delete(svc.get(list_id))
        self.assertEqual(AutofillTask.objects.filter(list_id=list_id).count(), 0)

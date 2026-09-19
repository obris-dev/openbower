"""The provisioner picks must READ their index in order and stop at the
LIMIT, never sort the whole READY set. This is the regression guard for
the lane split: a single (status, fill_run_id, list_id, ...) index looks
fine but the autofill firehose filters fill_run_id IS NULL, which gives a
btree no ordering pathkey, so it silently falls back to a full sort. Each
assertion forces seqscan off, isolating "can the index serve the order?"
from "does the planner prefer it at this data size".

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from django.db import connection
from django.db.models import Q
from django.test import TestCase
from django.utils import timezone

from ..constants import NodeRunStatus
from ..models import NodeRun
from ..nodes.registry import COLUMN_AGENT

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
FILL = "01FILL" + "0" * 20


class ProvisionIndexPlanTests(TestCase):
    def setUp(self) -> None:
        now = timezone.now()
        tasks = []
        for i in range(1500):  # autofill firehose across 10 lists
            tasks.append(
                NodeRun(
                    account_id=ACCOUNT,
                    kind=COLUMN_AGENT,
                    fill_run_id=None,
                    node_id="01NODEA" + "0" * 19,
                    row_id=f"01ROWA{i:020d}",
                    list_id=f"01LIST{i % 10:020d}",
                    position=i,
                    status=NodeRunStatus.READY,
                    last_state_change_at=now,
                )
            )
        for i in range(800):  # one fill-backed fill
            tasks.append(
                NodeRun(
                    account_id=ACCOUNT,
                    kind=COLUMN_AGENT,
                    fill_run_id=FILL,
                    node_id="01NODEM" + "0" * 19,
                    row_id=f"01ROWM{i:020d}",
                    list_id="01LIST" + "9" * 20,
                    position=i,
                    status=NodeRunStatus.READY,
                    last_state_change_at=now,
                )
            )
        NodeRun.objects.bulk_create(tasks)
        with connection.cursor() as c:
            c.execute("ANALYZE lists_noderun")
            c.execute("SET enable_seqscan=off")

    def _plan(self, qs) -> str:
        return qs.explain()

    def _base(self):
        now = timezone.now()
        due = Q(not_before__isnull=True) | Q(not_before__lte=now)
        return NodeRun.objects.filter(due, status=NodeRunStatus.READY).defer("result")

    def test_the_autofill_firehose_reads_its_index_in_order_never_sorts(self) -> None:
        plan = self._plan(
            self._base().filter(fill_run_id__isnull=True, kind=COLUMN_AGENT).order_by("list_id", "position", "id")[:64]
        )
        self.assertIn("node_run_autofill_idx", plan)
        self.assertNotIn("Sort", plan)  # the whole point: IS NULL must still stop at the LIMIT

    def test_a_sharded_autofill_pick_seeks_one_list(self) -> None:
        one_list = "01LIST" + "0" * 19 + "3"
        plan = self._plan(
            self._base()
            .filter(fill_run_id__isnull=True, kind=COLUMN_AGENT, list_id=one_list)
            .order_by("list_id", "position", "id")[:64]
        )
        self.assertIn("node_run_autofill_idx", plan)
        self.assertIn("list_id", plan)  # list_id is an index condition (a seek), not a post-filter
        self.assertNotIn("Sort", plan)

    def test_the_fill_backed_pick_reads_its_index_in_order_never_sorts(self) -> None:
        plan = self._plan(self._base().filter(fill_run_id=FILL).order_by("position", "id")[:64])
        self.assertIn("node_run_fill_idx", plan)
        self.assertNotIn("Sort", plan)

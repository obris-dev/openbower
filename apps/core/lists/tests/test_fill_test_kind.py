"""Test-kind fills: the bench riding the fill lane. Fencing first (a
test run must never leak onto a sheet surface), the lifecycle joining
as the lane lands.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fill_test_kind
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import ROW_LEASE_STALE_SECONDS, FillRunDetail

from ..constants import (
    MAX_ACTIVE_FILLS,
    TEST_FILL_MAX_AGE_SECONDS,
    FillKind,
    FillStatus,
    NodeRunStatus,
)
from ..models import Fill, ListRow, NodeRun
from ..operations.consume_node_runs import handle_node_run
from ..operations.sweep_test_fills import SweepTestFillsOperation
from ..services.fill_admission import (
    FillAdmissionService,
    TestFillActive,
    TestFillAdmission,
)
from ..services.fill_progress import live_fill_count
from ..services.fills import FillService
from ..services.lists import ListService
from ..services.node_runs import PROCESSING_STALE_SECONDS

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"


def quick_config(**overrides) -> AgentConfig:
    fields = {
        "prompt": "Find the answer for {{company}}",
        "provider": "openai_compatible",
        "source": "ollama",
        "model": "test-model",
        "tools": AgentTools(),
        "outputs": [AgentOutput(key="answer", label="Answer", type="text")],
    }
    fields.update(overrides)
    return AgentConfig(**fields)


def _test_fill() -> Fill:
    """A live test fill, built at the model level (the cap must count
    it whatever writer produced the row)."""
    fill = Fill.objects.create(
        account_id=ACCOUNT,
        user_id=USER,
        kind=FillKind.TEST,
        list_id="",
        agent_id="",
        column_keys=["answer"],
        config_snapshot={},
        confirmed_row_count=1,
        status=FillStatus.RUNNING,
    )
    NodeRun.objects.create(account_id=ACCOUNT, fill_run_id=str(fill.id), row_id="", position=0)
    return fill


class TestKindCapTests(TestCase):
    """A test fill never points at a sheet (list_id blank BY
    CONSTRUCTION), so every list-scoped fill read is structurally
    clear of the lane; what MUST still notice one is the account cap
    (a test fill is a fill for spend)."""

    def test_a_test_fill_counts_into_the_account_cap(self):
        before = live_fill_count(ACCOUNT)
        _test_fill()
        self.assertEqual(live_fill_count(ACCOUNT), before + 1)


class TestAdmissionTests(TestCase):
    """The test admission's own lifecycle: supersede, teammate
    refusal, and the shape of what it creates."""

    def setUp(self) -> None:
        self.admission = TestFillAdmission(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.fill_admission.base.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _admit(self, admission=None, **overrides):
        return (admission or self.admission).admit(config=quick_config(), row={"company": "acme.com"}, **overrides)

    def test_a_superseding_admission_never_holds_the_fill_row_lock(self):
        # The ABBA guard. stop_fill takes NodeRun before Fill (its
        # documented order, shared with the cancel view and the
        # worker's fail leg); an admission that held the Fill row lock
        # and THEN cancelled inverted that order and could deadlock a
        # concurrent stop of the very fill being superseded. FAILS if
        # the supersede scan grows a select_for_update on lists_fill
        # again.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._admit()
        with CaptureQueriesContext(connection) as captured:
            self._admit()
        locked = [
            q["sql"]
            for q in captured.captured_queries
            # Quoted, so LISTS_NODERUN does not match: the queue's own
            # locks are allowed, the fill row's are the hazard.
            if "FOR UPDATE" in q["sql"].upper() and '"LISTS_FILL"' in q["sql"].upper()
        ]
        self.assertEqual(locked, [], "the supersede admission must not lock fill rows")

    def test_an_inline_test_fill_carries_its_row_and_no_sheet_custody(self):
        fill = self._admit()
        self.assertEqual(fill.kind, FillKind.TEST)
        self.assertEqual((fill.list_id, fill.agent_id), ("", ""))
        self.assertEqual(fill.row_data, [{"company": "acme.com"}])
        self.assertEqual(fill.column_keys, ["answer"])
        self.assertTrue(fill.config_fingerprint)
        task = NodeRun.objects.get(fill_run_id=str(fill.id))
        # A MINTED row id, never "": the queue's (fill_run_id, row_id)
        # uniqueness would cap a blank-id lane at one task, against the
        # row_data list's grow-to-N shape. FAILS if the mint reverts.
        from openbower_kernel.fields import is_valid_ulid

        self.assertTrue(is_valid_ulid(task.row_id))
        # Born READY (the manual provisioner moves it to QUEUED on publish).
        self.assertEqual((task.position, task.status), (0, NodeRunStatus.READY))
        # list_id "" by construction: a bench fill points at no sheet, so
        # its task inherits the blank (mirrors fill.list_id above).
        self.assertEqual(task.list_id, "")

    def test_your_own_live_test_is_superseded_never_refused(self):
        first = self._admit()
        second = self._admit()
        first.refresh_from_db()
        self.assertEqual(first.status, FillStatus.CANCELLED)
        self.assertEqual(NodeRun.objects.get(fill_run_id=str(first.id)).status, NodeRunStatus.ABANDONED)
        self.assertEqual(second.status, FillStatus.PENDING)

    def test_a_teammates_fresh_test_refuses_with_its_code(self):
        # Fresh = too young to have missed a heartbeat: the ULID birth
        # stands in until the worker beats. FAILS if the guard loses
        # the teammate check or the freshness read.
        self._admit()
        teammate = TestFillAdmission(account_id=ACCOUNT, user_id="01USERBBBBBBBBBBBBBBBBBBBB")
        with self.assertRaises(TestFillActive):
            self._admit(admission=teammate)

    def test_a_teammates_stale_test_is_cancelled_and_superseded(self):
        stale = self._admit()
        # Freshness now derives from the tasks' last state change; aging
        # them past the lease window makes the run observably dead.
        NodeRun.objects.filter(fill_run_id=str(stale.id)).update(
            last_state_change_at=timezone.now() - timedelta(seconds=4096)
        )
        teammate = TestFillAdmission(account_id=ACCOUNT, user_id="01USERBBBBBBBBBBBBBBBBBBBB")
        fresh = self._admit(admission=teammate)
        stale.refresh_from_db()
        self.assertEqual(stale.status, FillStatus.CANCELLED)
        self.assertEqual(fresh.status, FillStatus.PENDING)

    def test_a_teammates_running_test_stays_fresh_within_the_processing_window(self) -> None:
        # A single-task test's last_state_change_at FREEZES while its
        # run_cell runs (no renewal), so freshness uses the PROCESSING
        # window (the reclaim's dead bound), not the tighter lease window.
        # A teammate's test aged past ROW_LEASE_STALE_SECONDS but within
        # PROCESSING_STALE_SECONDS is still RUNNING: it must refuse, not be
        # superseded mid-run. FAILS if the window reverts to the lease bound.
        running = self._admit()
        aged = ROW_LEASE_STALE_SECONDS + 60  # past the old window, well inside the processing one
        self.assertLess(aged, PROCESSING_STALE_SECONDS)
        NodeRun.objects.filter(fill_run_id=str(running.id)).update(
            last_state_change_at=timezone.now() - timedelta(seconds=aged)
        )
        teammate = TestFillAdmission(account_id=ACCOUNT, user_id="01USERBBBBBBBBBBBBBBBBBBBB")
        with self.assertRaises(TestFillActive):
            self._admit(admission=teammate)
        running.refresh_from_db()
        self.assertEqual(running.status, FillStatus.PENDING)  # not superseded

    def test_the_cron_sweep_purges_old_test_fills_and_only_those(self):
        old = self._admit()
        Fill.objects.filter(id=str(old.id)).update(status=FillStatus.COMPLETE)
        # Age the row past the TTL by faking its ULID-borne birth: the
        # sweep judges id ranges, so a manufactured old id is the seam.
        from openbower_kernel.fields import min_ulid_at

        old_id = min_ulid_at(timezone.now() - timedelta(seconds=TEST_FILL_MAX_AGE_SECONDS * 2))
        NodeRun.objects.filter(fill_run_id=str(old.id)).update(fill_run_id=old_id)
        Fill.objects.filter(id=str(old.id)).update(id=old_id)
        # An equally old NORMAL fill must survive: the sweep's kind
        # fence is the guard under test, and a count of exactly one
        # proves it fired without overreaching.
        normal_id = min_ulid_at(timezone.now() - timedelta(seconds=TEST_FILL_MAX_AGE_SECONDS * 3))
        Fill.objects.create(
            id=normal_id,
            account_id=ACCOUNT,
            user_id=USER,
            list_id="",
            agent_id="",
            column_keys=[],
            config_snapshot={},
            config_fingerprint="",
            confirmed_row_count=1,
        )
        keeper = self._admit()
        self.assertEqual(SweepTestFillsOperation().run(), 1)
        self.assertFalse(Fill.objects.filter(id=old_id).exists())
        self.assertFalse(NodeRun.objects.filter(fill_run_id=old_id).exists())
        self.assertTrue(Fill.objects.filter(id=normal_id).exists())
        self.assertTrue(Fill.objects.filter(id=str(keeper.id)).exists())

    def test_a_test_admission_never_hears_the_account_cap(self):
        # The bench must always answer: an account manufactured AT the
        # fill cap still admits a test (supersede bounds the lane at
        # one live test advisorily, so the overshoot stays small). FAILS if
        # the account cap ever returns to the test admission.
        for _ in range(MAX_ACTIVE_FILLS):
            Fill.objects.create(
                account_id=ACCOUNT,
                user_id=USER,
                list_id="",
                agent_id="",
                column_keys=[],
                config_snapshot={},
                config_fingerprint="",
                confirmed_row_count=1,
            )
        fill = self._admit()
        self.assertEqual(fill.status, FillStatus.PENDING)


class TestFillEndpointTests(TestCase):
    """The /v1/fills surface through real cookie auth: bounds refuse
    with 400s, create answers 202, a foreign run reads as missing."""

    def setUp(self) -> None:
        login_session(self.client)
        self.account = TEST_IDENTITY["account_id"]
        self.user = TEST_IDENTITY["id"]

    def _post(self, body):
        with patch("lists.services.fill_admission.base.model_for"):
            return self.client.post(reverse("fills_test"), body, content_type="application/json")

    def _config(self):
        return {
            "prompt": "Find the answer for {{company}}",
            "provider": "openai_compatible",
            "source": "ollama",
            "model": "test-model",
            "tools": {},
            "outputs": [{"label": "Answer", "type": "text"}],
        }

    def test_create_answers_202_with_the_detail_envelope(self):
        resp = self._post({"config": self._config(), "row": {"company": "acme.com"}})
        self.assertEqual(resp.status_code, 202, resp.content)
        wire = FillRunDetail.model_validate(resp.json())
        self.assertEqual((wire.kind, wire.result, wire.status), ("test", None, "pending"))

    def test_bounds_refuse_never_truncate(self):
        # The envelope is asserted, not just the status: a DRF-shaped
        # 400 carries no `detail`, so the client renders its generic
        # fallback instead of the authored copy. FAILS if the bound
        # refusals leave the {error, detail} envelope again.
        too_many = {f"k{n}": "v" for n in range(17)}
        long_key = {"k" * 65: "v"}
        long_value = {"company": "v" * 513}
        for row in (too_many, long_key, long_value):
            with self.subTest(row=next(iter(row))[:8]):
                resp = self._post({"config": self._config(), "row": row})
                self.assertEqual(resp.status_code, 400)
                body = resp.json()
                self.assertEqual(body["error"], "test_row_invalid")
                self.assertTrue(body["detail"])
        self.assertEqual(Fill.objects.count(), 0)

    def test_a_missing_row_refuses(self):
        self.assertEqual(self._post({"config": self._config()}).status_code, 400)

    def test_an_unrunnable_address_refuses_at_post(self):
        # Config tier refuses BEFORE a run exists, exactly as the old
        # bench gate did: never a started run that crashes per row.
        from agents.providers import ModelUnavailable

        with patch(
            "lists.services.fill_admission.base.model_for", side_effect=ModelUnavailable("source unknown or closed")
        ):
            resp = self.client.post(
                reverse("fills_test"),
                {"config": {**self._config(), "source": "ghost"}, "row": {"company": "acme.com"}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("source unknown or closed", resp.json()["detail"])
        self.assertEqual(Fill.objects.count(), 0)

    def test_a_non_string_row_value_refuses(self):
        # The row is a DictField of CharFields: structured values are a
        # contract violation, never something to coerce quietly.
        resp = self._post({"config": self._config(), "row": {"company": {"nested": "no"}}})
        self.assertEqual(resp.status_code, 400)

    def test_a_foreign_run_reads_as_missing(self):
        foreign = Fill.objects.create(
            account_id="01ACCOUNTBBBBBBBBBBBBBBBBB",
            user_id=self.user,
            kind=FillKind.TEST,
            list_id="",
            agent_id="",
            column_keys=["answer"],
            config_snapshot={},
            confirmed_row_count=1,
        )
        resp = self.client.get(reverse("fills_detail", args=[str(foreign.id)]))
        self.assertEqual(resp.status_code, 404)

    def test_cancel_stops_a_listless_test_fill(self):
        created = self._post({"config": self._config(), "row": {"company": "acme.com"}}).json()
        resp = self.client.post(reverse("fills_cancel", args=[created["id"]]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cancelled")


class TestKindWorkerTests(TransactionTestCase):
    """The test lane end to end through the REAL consumer machinery:
    claim, run, task-borne landing, completion. One shared consumer
    handles both kinds (it branches on the task's fill), so the test
    kind lands on its task and never on a sheet. TransactionTestCase for
    the consumer's own DB work (the fill worker tests' rule)."""

    def setUp(self) -> None:
        self.admission = FillAdmissionService(account_id=ACCOUNT, user_id=USER)
        self.tests = TestFillAdmission(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.fill_admission.base.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_fill(self, fill, model) -> None:
        from .test_fill_worker import _patches

        with _patches(model):
            ids = list(
                NodeRun.objects.filter(
                    fill_run_id=str(fill.id),
                    status__in=(NodeRunStatus.READY, NodeRunStatus.QUEUED),
                )
                .order_by("position")
                .values_list("id", flat=True)
            )
            for task_id in ids:
                handle_node_run(str(task_id), "test:kind")

    def test_an_inline_test_runs_end_to_end_landing_on_its_task(self):
        from ..services.fills import derive_counters
        from .test_fill_worker import answering_model

        fill = self.tests.admit(config=quick_config(), row={"company": "acme.com"})
        self._run_fill(fill, answering_model(lambda prompt: "found: " + prompt.split()[-1]))
        fill.refresh_from_db()
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        # The one task settled DONE; attempted DERIVES from it (a test
        # fill writes no sheet cell, so filled stays 0).
        counters = derive_counters(fill)
        self.assertEqual((counters.attempted, counters.filled), (1, 0))
        result = FillService(account_id=ACCOUNT).test_result(fill)
        self.assertIsNotNone(result)
        self.assertEqual(result.cells["answer"], "found: acme.com")
        # No sheet artifacts, ever: the landing branch never writes one.
        from ..models import ListCellState

        self.assertFalse(ListCellState.objects.exists())

    def test_the_shared_consumer_runs_both_kinds(self):
        # One consumer serves the manual lane and routes on the task's
        # own fill: a NORMAL fill lands on its sheet, a TEST fill on its
        # task, both driven through the same handle_node_run.
        from .test_fill_worker import answering_model

        lists = ListService(account_id=ACCOUNT)
        sheet = lists.create(
            owner_id=USER, label="P", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        lists.add_rows(sheet, [{"company": "acme.com"}])
        real = self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        test = self.tests.admit(config=quick_config(), row={"company": "acme.com"})
        self._run_fill(real, answering_model(lambda prompt: "x"))
        self._run_fill(test, answering_model(lambda prompt: "x"))
        real.refresh_from_db()
        test.refresh_from_db()
        self.assertEqual((real.status, test.status), (FillStatus.COMPLETE, FillStatus.COMPLETE))
        # The test fill's result lands on its task; the normal fill's on
        # the sheet.
        self.assertIsNotNone(FillService(account_id=ACCOUNT).test_result(test))
        self.assertEqual(ListRow.objects.get(list_id=str(sheet.id)).data["answer"], "x")

"""Bench runs: the agent builder's one-row diagnostic as ONE NodeRun
that owns its input. The lane's shape and supersede rule, the /v1/runs
surface, the run end to end through the real consumer (landing on
itself, never on a sheet), and the age prune.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_bench_runs
"""

from __future__ import annotations

from datetime import timedelta
from typing import get_args
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import ROW_LEASE_STALE_SECONDS, CellRunResult
from openbower_schema.runs import OPEN_NODE_RUN_STATES, NodeRunStatusWire, NodeRunWire

from ..constants import (
    BENCH_RUN_MAX_AGE_SECONDS,
    NODE_RUN_ATTEMPTS,
    NON_TERMINAL_NODE_RUN_STATES,
    NodeRunStatus,
    StoredCellState,
)
from ..models import ListCellState, ListRow, Node, NodeRun
from ..nodes.registry import COLUMN_AGENT
from ..operations.consume_node_runs import handle_node_run
from ..operations.prune_bench_runs import PruneBenchRunsOperation
from ..services.bench_runs import BenchActive, BenchRunNotFound, BenchRunService, BenchUnrunnable
from ..services.fill_progress import open_fill_count
from ..services.node_runs import PROCESSING_STALE_SECONDS, NodeRunFlow

ACCOUNT = "01ACCOUNTAAAAAAAAAAAAAAAAA"
USER = "01USERAAAAAAAAAAAAAAAAAAAA"
TEAMMATE = "01USERBBBBBBBBBBBBBBBBBBBB"


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


class RunStatusParityPins(SimpleTestCase):
    def test_the_wire_vocabulary_is_the_ledgers(self):
        # The contract's status literal and its OPEN partition are the
        # server's NodeRunStatus and NON_TERMINAL set, member for member:
        # the bench poll derives its loop predicate off the wire, so a
        # status added on one side only would either never terminate a
        # poll or end one early.
        self.assertEqual(set(get_args(NodeRunStatusWire)), {str(status) for status in NodeRunStatus})
        self.assertEqual(set(OPEN_NODE_RUN_STATES), {str(status) for status in NON_TERMINAL_NODE_RUN_STATES})


class BenchRunServiceTests(TestCase):
    """The lane's own lifecycle: the shape it creates, supersede, the
    teammate refusal, cancel, and the result read."""

    def setUp(self) -> None:
        self.bench = BenchRunService(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.bench_runs.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start(self, service: BenchRunService | None = None, **overrides) -> NodeRun:
        return (service or self.bench).start(config=quick_config(), row={"company": "acme.com"}, **overrides)

    def test_a_bench_run_owns_its_input_and_points_at_no_sheet(self):
        run = self._start()
        self.assertEqual(run.input, {"row": {"company": "acme.com"}, "config": quick_config().model_dump()})
        self.assertEqual(run.started_by, USER)
        # row_id NULL: the row rides `input` and no ListRow exists for
        # it; list_id "": no sheet by construction. The rank is the first
        # key, as every run holds one; alone off any sheet, that is its place.
        self.assertIsNone(run.row_id)
        self.assertIsNone(run.fill_run_id)
        self.assertEqual((run.list_id, run.rank, run.status), ("", "a0", NodeRunStatus.READY))
        # The run's node is the account's bench node: the one sheetless
        # column_agent node, no workflow, no path, a blank agent.
        bench = Node.objects.get(account_id=ACCOUNT, workflow_id="")
        self.assertEqual(run.node_id, str(bench.id))
        self.assertEqual((bench.kind, bench.path_id, bench.config), (COLUMN_AGENT, "", {"agent_id": ""}))

    def test_every_bench_run_reuses_the_one_bench_node(self):
        first = self._start()
        second = self._start()
        self.assertEqual(Node.objects.filter(account_id=ACCOUNT, workflow_id="").count(), 1)
        self.assertEqual(first.node_id, second.node_id)

    def test_the_autofill_pick_carries_it(self):
        # The bench rides the automatic lane: the autofill provisioner's
        # READY null-fill agent pick takes it with no routing of its own.
        run = self._start()
        self.assertIn(str(run.id), [str(picked.id) for picked in NodeRunFlow.iter_ready(limit=10)])

    def test_bounds_refuse_and_create_nothing(self):
        from ..services.bench_runs import BenchRowInvalid

        too_many = {f"k{n}": "v" for n in range(17)}
        long_key = {"k" * 65: "v"}
        long_value = {"company": "v" * 513}
        for row in (too_many, long_key, long_value):
            with self.subTest(row=next(iter(row))[:8]), self.assertRaises(BenchRowInvalid):
                self.bench.start(config=quick_config(), row=row)
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_an_unrunnable_config_refuses_before_a_run_exists(self):
        from agents.providers import ModelUnavailable

        with (
            patch("lists.services.bench_runs.model_for", side_effect=ModelUnavailable("source closed")),
            self.assertRaises(BenchUnrunnable),
        ):
            self._start()
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_your_own_live_run_is_superseded_never_refused(self):
        first = self._start()
        second = self._start()
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), (NodeRunStatus.ABANDONED, NodeRunStatus.READY))

    def test_a_teammates_fresh_run_refuses_with_its_code(self):
        self._start()
        with self.assertRaises(BenchActive):
            self._start(BenchRunService(account_id=ACCOUNT, user_id=TEAMMATE))

    def test_a_teammates_stale_run_is_abandoned_and_superseded(self):
        stale = self._start()
        NodeRun.objects.filter(id=stale.id).update(last_state_change_at=timezone.now() - timedelta(seconds=4096))
        fresh = self._start(BenchRunService(account_id=ACCOUNT, user_id=TEAMMATE))
        stale.refresh_from_db()
        self.assertEqual((stale.status, fresh.status), (NodeRunStatus.ABANDONED, NodeRunStatus.READY))

    def test_a_teammates_running_run_stays_fresh_within_the_processing_window(self):
        # A run's last_state_change_at FREEZES while its run_cell runs
        # (no renewal), so freshness uses the PROCESSING window (the
        # reclaim's dead bound), not the tighter lease window. FAILS if
        # the window reverts to the lease bound.
        running = self._start()
        aged = ROW_LEASE_STALE_SECONDS + 60
        self.assertLess(aged, PROCESSING_STALE_SECONDS)
        NodeRun.objects.filter(id=running.id).update(last_state_change_at=timezone.now() - timedelta(seconds=aged))
        with self.assertRaises(BenchActive):
            self._start(BenchRunService(account_id=ACCOUNT, user_id=TEAMMATE))
        running.refresh_from_db()
        self.assertEqual(running.status, NodeRunStatus.READY)

    def test_a_bench_run_never_counts_into_the_fill_cap(self):
        before = open_fill_count(ACCOUNT)
        self._start()
        self.assertEqual(open_fill_count(ACCOUNT), before)

    def test_cancel_abandons_an_unclaimed_run_and_leaves_a_finished_one(self):
        run = self._start()
        self.assertEqual(self.bench.cancel(str(run.id)).status, NodeRunStatus.ABANDONED)
        finished = self._start()
        NodeRun.objects.filter(id=finished.id).update(status=NodeRunStatus.DONE, result={"cells": {"answer": "x"}})
        self.assertEqual(self.bench.cancel(str(finished.id)).status, NodeRunStatus.DONE)

    def test_a_foreign_or_non_bench_run_reads_as_missing(self):
        run = self._start()
        with self.assertRaises(BenchRunNotFound):
            BenchRunService(account_id="01ACCOUNTBBBBBBBBBBBBBBBBB", user_id=USER).get(str(run.id))
        # An autofill run (a row, no input) is not a bench run, whatever
        # its account.
        autofill = NodeRun.objects.create(
            account_id=ACCOUNT, node_id=run.node_id, kind=COLUMN_AGENT, row_id="01ROWAAAAAAAAAAAAAAAAAAAAA", rank="a0"
        )
        with self.assertRaises(BenchRunNotFound):
            self.bench.get(str(autofill.id))

    def test_the_result_reads_only_off_a_finished_run(self):
        run = self._start()
        self.assertIsNone(self.bench.result(run))
        NodeRun.objects.filter(id=run.id).update(status=NodeRunStatus.DONE, result={"cells": {"answer": "x"}})
        run.refresh_from_db()
        self.assertEqual(self.bench.result(run).cells, {"answer": "x"})


class BenchEndpointTests(TestCase):
    """The /v1/runs surface through real cookie auth: bounds refuse
    with 400s, start answers 202, a foreign run reads as missing."""

    def setUp(self) -> None:
        login_session(self.client)
        self.account = TEST_IDENTITY["account_id"]
        self.user = TEST_IDENTITY["id"]

    def _post(self, body):
        with patch("lists.services.bench_runs.model_for"):
            return self.client.post(reverse("runs_bench"), body, content_type="application/json")

    def _config(self):
        return {
            "prompt": "Find the answer for {{company}}",
            "provider": "openai_compatible",
            "source": "ollama",
            "model": "test-model",
            "tools": {},
            "outputs": [{"label": "Answer", "type": "text"}],
        }

    def test_start_answers_202_with_the_run_envelope(self):
        resp = self._post({"config": self._config(), "row": {"company": "acme.com"}})
        self.assertEqual(resp.status_code, 202, resp.content)
        wire = NodeRunWire.model_validate(resp.json())
        self.assertEqual((wire.status, wire.result), ("ready", None))

    def test_bounds_refuse_never_truncate(self):
        # The envelope is asserted, not just the status: a DRF-shaped
        # 400 carries no `detail`, so the client renders its generic
        # fallback instead of the authored copy.
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
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_a_missing_or_structured_row_refuses(self):
        self.assertEqual(self._post({"config": self._config()}).status_code, 400)
        resp = self._post({"config": self._config(), "row": {"company": {"nested": "no"}}})
        self.assertEqual(resp.status_code, 400)

    def test_an_unrunnable_address_refuses_at_post(self):
        from agents.providers import ModelUnavailable

        with patch("lists.services.bench_runs.model_for", side_effect=ModelUnavailable("source unknown or closed")):
            resp = self.client.post(
                reverse("runs_bench"),
                {"config": {**self._config(), "source": "ghost"}, "row": {"company": "acme.com"}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "model_unrunnable")
        self.assertIn("source unknown or closed", resp.json()["detail"])
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_a_teammates_live_run_answers_409(self):
        with patch("lists.services.bench_runs.model_for"):
            BenchRunService(account_id=self.account, user_id="01USERZZZZZZZZZZZZZZZZZZZZ").start(
                config=quick_config(), row={"company": "acme.com"}
            )
        resp = self._post({"config": self._config(), "row": {"company": "acme.com"}})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "test_active")

    def test_a_foreign_run_reads_as_missing(self):
        with patch("lists.services.bench_runs.model_for"):
            foreign = BenchRunService(account_id="01ACCOUNTBBBBBBBBBBBBBBBBB", user_id=self.user).start(
                config=quick_config(), row={"company": "acme.com"}
            )
        self.assertEqual(self.client.get(reverse("runs_detail", args=[str(foreign.id)])).status_code, 404)

    def test_cancel_abandons_the_run_and_echoes_it(self):
        created = self._post({"config": self._config(), "row": {"company": "acme.com"}}).json()
        resp = self.client.post(reverse("runs_cancel", args=[created["id"]]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "abandoned")
        self.assertEqual(self.client.get(reverse("runs_detail", args=[created["id"]])).json()["status"], "abandoned")


class BenchWorkerTests(TransactionTestCase):
    """The bench end to end through the REAL consumer machinery: claim,
    run, landing on the run itself, never on a sheet. TransactionTestCase
    for the consumer's own DB work (the fill worker tests' rule)."""

    def setUp(self) -> None:
        self.bench = BenchRunService(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.bench_runs.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, run: NodeRun, model) -> None:
        from .test_fill_worker import _patches

        with _patches(model):
            handle_node_run(str(run.id), "test:bench")

    def test_a_bench_run_lands_its_result_on_itself(self):
        from .test_fill_worker import answering_model

        run = self.bench.start(config=quick_config(), row={"company": "acme.com"})
        self._run(run, answering_model(lambda prompt: "found: " + prompt.split()[-1]))
        run.refresh_from_db()
        self.assertEqual(run.status, NodeRunStatus.DONE)
        self.assertEqual(self.bench.result(run).cells["answer"], "found: acme.com")
        # No sheet artifacts, ever: no row, no cell truth.
        self.assertFalse(ListCellState.objects.exists())
        self.assertFalse(ListRow.objects.exists())

    def test_an_exhausted_bench_run_gives_up_on_itself_with_its_cause(self):
        from .test_fill_worker import answering_model

        run = self.bench.start(config=quick_config(), row={"company": "acme.com"})
        prior = CellRunResult(declined_cause=StoredCellState.TRANSIENT)
        NodeRun.objects.filter(id=run.id).update(attempts=NODE_RUN_ATTEMPTS, result=prior.model_dump())
        self._run(run, answering_model(lambda prompt: "never reached"))
        run.refresh_from_db()
        self.assertEqual(run.status, NodeRunStatus.DONE)
        self.assertEqual(self.bench.result(run).declined_cause, StoredCellState.TRANSIENT)
        self.assertFalse(ListCellState.objects.exists())

    def test_the_prune_purges_old_bench_runs_and_only_those(self):
        from openbower_kernel.fields import min_ulid_at

        old = self.bench.start(config=quick_config(), row={"company": "acme.com"})
        old_id = min_ulid_at(timezone.now() - timedelta(seconds=BENCH_RUN_MAX_AGE_SECONDS * 2))
        NodeRun.objects.filter(id=old.id).update(id=old_id)
        # An equally old AUTOFILL run must survive: the prune's shape
        # fence (no row, no fill, of the agent kind) is the guard.
        autofill_id = min_ulid_at(timezone.now() - timedelta(seconds=BENCH_RUN_MAX_AGE_SECONDS * 3))
        NodeRun.objects.create(
            id=autofill_id,
            account_id=ACCOUNT,
            node_id=old.node_id,
            kind=COLUMN_AGENT,
            row_id="01ROWAAAAAAAAAAAAAAAAAAAAA",
            rank="a0",
            status=NodeRunStatus.DONE,
        )
        keeper = self.bench.start(config=quick_config(), row={"company": "acme.com"})
        self.assertEqual(PruneBenchRunsOperation().run(), 1)
        self.assertFalse(NodeRun.objects.filter(id=old_id).exists())
        self.assertTrue(NodeRun.objects.filter(id=autofill_id).exists())
        self.assertTrue(NodeRun.objects.filter(id=keeper.id).exists())

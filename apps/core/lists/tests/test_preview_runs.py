"""Preview runs: the agent builder's one-row diagnostic as ONE NodeRun
that owns its input. The lane's shape and supersede rule, the /v1/runs
surface, the run end to end through the real consumer (landing on
itself, never on a sheet), and the age prune.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_preview_runs
"""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import CellRunResult
from openbower_schema.runs import NodeRunWire

from ..constants import (
    MAX_ACTIVE_FILLS,
    NODE_RUN_ATTEMPTS,
    PREVIEW_RUN_MAX_AGE_SECONDS,
    NodeRunStatus,
    StoredCellState,
)
from ..models import ListCellState, ListRow, Node, NodeRun
from ..nodes.registry import COLUMN_AGENT
from ..operations.consume_node_runs import handle_node_run
from ..operations.prune_preview_runs import PrunePreviewRunsOperation
from ..services.fill_progress import open_fill_count
from ..services.node_runs import NodeRunFlow
from ..services.preview_runs import (
    PreviewRunNotFound,
    PreviewRunService,
    PreviewUnrunnable,
    preview_runs,
)
from .fill_helpers import open_fill_job

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


class PreviewRunServiceTests(TestCase):
    """The lane's own lifecycle: the shape it creates, supersede, the
    teammate refusal, cancel, and the result read."""

    def setUp(self) -> None:
        self.preview = PreviewRunService(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.runnable.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start(self, service: PreviewRunService | None = None, **overrides) -> NodeRun:
        return (service or self.preview).start(config=quick_config(), row={"company": "acme.com"}, **overrides)

    def test_a_preview_run_owns_its_input_and_points_at_no_sheet(self):
        run = self._start()
        self.assertEqual(run.input, {"row": {"company": "acme.com"}, "config": quick_config().model_dump()})
        self.assertEqual(run.started_by, USER)
        # row_id NULL: the row rides `input` and no ListRow exists for
        # it; list_id "": no sheet by construction. The rank is the first
        # key, as every run holds one; alone off any sheet, that is its place.
        self.assertIsNone(run.row_id)
        self.assertIsNone(run.fill_run_id)
        self.assertEqual((run.list_id, run.rank, run.status), ("", "a0", NodeRunStatus.READY))
        # The run's node is the account's preview node: the one sheetless
        # column_agent node, no workflow, no path, a blank agent.
        preview = Node.objects.get(account_id=ACCOUNT, workflow_id="")
        self.assertEqual(run.node_id, str(preview.id))
        self.assertEqual((preview.kind, preview.path_id, preview.config), (COLUMN_AGENT, "", {"agent_id": ""}))

    def test_a_sheet_run_has_no_starter(self):
        # NULL, not "": the job rule for an absent user. FAILS if the
        # column regains a blank default.
        run = NodeRun.objects.create(
            account_id=ACCOUNT, node_id="01NODE" + "0" * 20, kind=COLUMN_AGENT, row_id="01ROW" + "0" * 21, rank="a0"
        )
        run.refresh_from_db()
        self.assertIsNone(run.started_by)

    def test_every_preview_run_reuses_the_one_preview_node(self):
        first = self._start()
        second = self._start()
        self.assertEqual(Node.objects.filter(account_id=ACCOUNT, workflow_id="").count(), 1)
        self.assertEqual(first.node_id, second.node_id)

    def test_the_pick_routes_it_to_the_preview_topic_ahead_of_the_rows(self):
        # One pick, two buses: the preview run is published to the
        # preview topic, a pushed row's run to the autofill topic, in one
        # pass. FAILS if the routing collapses to one topic.
        from unittest.mock import MagicMock, patch

        from lists.ingest.topics import AUTOFILL_RUNS, PREVIEW_RUNS
        from lists.operations.provision.autofill import AutofillProvisionOperation

        preview = self._start()
        sheet_run = NodeRun.objects.create(
            account_id=ACCOUNT,
            node_id=preview.node_id,
            kind=COLUMN_AGENT,
            row_id="01ROW" + "0" * 21,
            list_id="01LIST" + "0" * 20,
            rank="a0",
            status=NodeRunStatus.READY,
            last_state_change_at=timezone.now(),
        )
        producer = MagicMock()
        producer.flush.return_value = 0
        with patch("confluent_kafka.Producer", return_value=producer):
            AutofillProvisionOperation(worker_id="test:prov", stop=threading.Event()).run(once=True)
        published = {}
        for call in producer.produce.call_args_list:
            topic = call.args[0] if call.args else call.kwargs["topic"]
            key = call.kwargs.get("key", call.args[1] if len(call.args) > 1 else None)
            published[bytes(key).decode() if isinstance(key, (bytes, bytearray)) else str(key)] = topic
        self.assertEqual(published.get(str(preview.id)), PREVIEW_RUNS.name)
        self.assertEqual(published.get(str(sheet_run.id)), AUTOFILL_RUNS.name)
        self.assertEqual(
            {str(run.id): run.status for run in NodeRun.objects.filter(id__in=[preview.id, sheet_run.id])},
            {str(preview.id): NodeRunStatus.QUEUED, str(sheet_run.id): NodeRunStatus.QUEUED},
        )

    def test_is_preview_and_the_queryset_spell_one_fact(self):
        preview = self._start()
        self.assertTrue(preview.is_preview)
        self.assertEqual(list(preview_runs().values_list("id", flat=True)), [preview.id])

    def test_the_autofill_pick_carries_it(self):
        # The preview rides the automatic lane: the autofill provisioner's
        # READY null-fill agent pick takes it with no routing of its own.
        run = self._start()
        self.assertIn(str(run.id), [str(picked.id) for picked in NodeRunFlow.iter_ready(limit=10)])

    def test_bounds_refuse_and_create_nothing(self):
        from ..services.preview_runs import PreviewRowInvalid

        too_many = {f"k{n}": "v" for n in range(17)}
        long_key = {"k" * 65: "v"}
        long_value = {"company": "v" * 513}
        for row in (too_many, long_key, long_value):
            with self.subTest(row=next(iter(row))[:8]), self.assertRaises(PreviewRowInvalid):
                self.preview.start(config=quick_config(), row=row)
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_an_unrunnable_config_refuses_before_a_run_exists(self):
        from agents.providers import ModelUnavailable

        with (
            patch("lists.services.runnable.model_for", side_effect=ModelUnavailable("source closed")),
            self.assertRaises(PreviewUnrunnable),
        ):
            self._start()
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_your_own_live_run_is_superseded_never_refused(self):
        first = self._start()
        second = self._start()
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), (NodeRunStatus.ABANDONED, NodeRunStatus.READY))

    def test_a_teammates_live_run_is_untouched_and_yours_starts(self):
        # The rule is per user: a teammate's unclaimed run is neither
        # refused against nor abandoned, and yours starts beside it.
        # FAILS if the supersede reads anyone else's runs.
        theirs = self._start(PreviewRunService(account_id=ACCOUNT, user_id=TEAMMATE))
        mine = self._start()
        theirs.refresh_from_db()
        self.assertEqual((theirs.status, mine.status), (NodeRunStatus.READY, NodeRunStatus.READY))
        self.assertEqual(NodeRun.objects.count(), 2)

    def test_the_supersede_rides_the_open_run_key(self):
        # The read must state the open states and the null fill, or it
        # falls off the open-run key's partial index onto a scan of
        # every preview run. FAILS if either predicate is dropped.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._start()
        with CaptureQueriesContext(connection) as captured:
            self._start()
        supersede = [q["sql"] for q in captured.captured_queries if "started_by" in q["sql"] and "SELECT" in q["sql"]]
        self.assertEqual(len(supersede), 1, supersede)
        self.assertIn("IS NULL", supersede[0])
        self.assertIn("'ready'", supersede[0])
        self.assertIn("'queued'", supersede[0])
        self.assertNotIn("'processing'", supersede[0])

    def test_a_claimed_run_is_never_abandoned_by_cancel_or_supersede(self):
        # A run a consumer owns runs to its own terminal CAS (in-flight
        # spend is sunk cost, and its result still lands): neither the
        # cancel nor your own next start reaches PROCESSING. FAILS if
        # abandon_runs widens past READY | QUEUED.
        running = self._start()
        NodeRun.objects.filter(id=running.id).update(status=NodeRunStatus.PROCESSING)
        self.assertEqual(self.preview.cancel(str(running.id)).status, NodeRunStatus.PROCESSING)
        self._start()
        running.refresh_from_db()
        self.assertEqual(running.status, NodeRunStatus.PROCESSING)

    def test_the_open_run_key_tolerates_two_live_preview_runs(self):
        # node_run_open_uniq keys (row_id, node_id) over the open null-
        # fill runs; a preview run's row_id is NULL, and NULLs are
        # distinct, so two starts that both pass the advisory scan leave
        # two live runs (the next click supersedes both) rather than
        # crashing the second on the key. FAILS if the preview ever
        # writes a non-null row placeholder.
        first = self._start()
        second = NodeRun.objects.create(
            account_id=ACCOUNT,
            node_id=first.node_id,
            kind=COLUMN_AGENT,
            row_id=first.row_id,
            list_id=first.list_id,
            rank=first.rank,
            status=NodeRunStatus.READY,
            input=first.input,
            started_by=USER,
            last_state_change_at=timezone.now(),
        )
        self.assertEqual(
            set(preview_runs().filter(status=NodeRunStatus.READY).values_list("id", flat=True)),
            {first.id, second.id},
        )

    def test_a_preview_run_neither_takes_nor_needs_a_fill_slot(self):
        # The cap is a count of open FILL JOBS: an account at the cap can
        # still test a draft, and the test's run takes no slot. FAILS if
        # the start ever consults the cap or the count ever reads runs.
        for n in range(MAX_ACTIVE_FILLS):
            open_fill_job(
                account_id=ACCOUNT,
                user_id=USER,
                list_id=f"01LIST{n:020d}",
                node_id=f"01NODE{n:020d}",
                agent_id=f"01AGENT{n:019d}",
                column_keys=["answer"],
                consented=1,
            )
        self.assertEqual(open_fill_count(ACCOUNT), MAX_ACTIVE_FILLS)
        run = self._start()
        self.assertEqual(run.status, NodeRunStatus.READY)
        self.assertEqual(open_fill_count(ACCOUNT), MAX_ACTIVE_FILLS)

    def test_cancel_abandons_an_unclaimed_run_and_leaves_a_finished_one(self):
        run = self._start()
        self.assertEqual(self.preview.cancel(str(run.id)).status, NodeRunStatus.ABANDONED)
        finished = self._start()
        NodeRun.objects.filter(id=finished.id).update(status=NodeRunStatus.DONE, result={"cells": {"answer": "x"}})
        self.assertEqual(self.preview.cancel(str(finished.id)).status, NodeRunStatus.DONE)

    def test_a_foreign_or_non_preview_run_reads_as_missing(self):
        run = self._start()
        with self.assertRaises(PreviewRunNotFound):
            PreviewRunService(account_id="01ACCOUNTBBBBBBBBBBBBBBBBB", user_id=USER).get(str(run.id))
        # An autofill run (a row, no input) is not a preview run, whatever
        # its account.
        autofill = NodeRun.objects.create(
            account_id=ACCOUNT, node_id=run.node_id, kind=COLUMN_AGENT, row_id="01ROWAAAAAAAAAAAAAAAAAAAAA", rank="a0"
        )
        with self.assertRaises(PreviewRunNotFound):
            self.preview.get(str(autofill.id))

    def test_the_result_reads_only_off_a_finished_run(self):
        run = self._start()
        self.assertIsNone(self.preview.result(run))
        NodeRun.objects.filter(id=run.id).update(status=NodeRunStatus.DONE, result={"cells": {"answer": "x"}})
        run.refresh_from_db()
        self.assertEqual(self.preview.result(run).cells, {"answer": "x"})


class PreviewEndpointTests(TestCase):
    """The /v1/runs surface through real cookie auth: bounds refuse
    with 400s, start answers 202, a foreign run reads as missing."""

    def setUp(self) -> None:
        login_session(self.client)
        self.account = TEST_IDENTITY["account_id"]
        self.user = TEST_IDENTITY["id"]

    def _post(self, body):
        with patch("lists.services.runnable.model_for"):
            return self.client.post(reverse("runs_preview"), body, content_type="application/json")

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

        with patch("lists.services.runnable.model_for", side_effect=ModelUnavailable("source unknown or closed")):
            resp = self.client.post(
                reverse("runs_preview"),
                {"config": {**self._config(), "source": "ghost"}, "row": {"company": "acme.com"}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "model_unrunnable")
        self.assertIn("source unknown or closed", resp.json()["detail"])
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_a_teammates_live_run_never_refuses_a_post(self):
        with patch("lists.services.runnable.model_for"):
            theirs = PreviewRunService(account_id=self.account, user_id="01USERZZZZZZZZZZZZZZZZZZZZ").start(
                config=quick_config(), row={"company": "acme.com"}
            )
        resp = self._post({"config": self._config(), "row": {"company": "acme.com"}})
        self.assertEqual(resp.status_code, 202, resp.content)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, NodeRunStatus.READY)

    def test_a_foreign_run_reads_as_missing(self):
        with patch("lists.services.runnable.model_for"):
            foreign = PreviewRunService(account_id="01ACCOUNTBBBBBBBBBBBBBBBBB", user_id=self.user).start(
                config=quick_config(), row={"company": "acme.com"}
            )
        self.assertEqual(self.client.get(reverse("runs_detail", args=[str(foreign.id)])).status_code, 404)

    def test_the_result_crosses_the_wire_once_the_run_finished(self):
        # The stored result rides GET as the contract's CellRunResult, and
        # only once the run is DONE. FAILS if the view stops handing the
        # service's read to the wire.
        created = self._post({"config": self._config(), "row": {"company": "acme.com"}}).json()
        detail = reverse("runs_detail", args=[created["id"]])
        self.assertIsNone(NodeRunWire.model_validate(self.client.get(detail).json()).result)
        NodeRun.objects.filter(id=created["id"]).update(
            status=NodeRunStatus.DONE, result=CellRunResult(cells={"answer": "x"}).model_dump()
        )
        wire = NodeRunWire.model_validate(self.client.get(detail).json())
        self.assertEqual((wire.status, wire.result.cells), ("done", {"answer": "x"}))

    def test_cancel_abandons_the_run_and_echoes_it(self):
        created = self._post({"config": self._config(), "row": {"company": "acme.com"}}).json()
        resp = self.client.post(reverse("runs_cancel", args=[created["id"]]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "abandoned")
        self.assertEqual(self.client.get(reverse("runs_detail", args=[created["id"]])).json()["status"], "abandoned")


class PreviewWorkerTests(TransactionTestCase):
    """The preview end to end through the REAL consumer machinery: claim,
    run, landing on the run itself, never on a sheet. TransactionTestCase
    for the consumer's own DB work (the fill worker tests' rule)."""

    def setUp(self) -> None:
        self.preview = PreviewRunService(account_id=ACCOUNT, user_id=USER)
        patcher = patch("lists.services.runnable.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, run: NodeRun, model) -> None:
        from .test_fill_worker import _patches

        with _patches(model):
            handle_node_run(str(run.id), "test:preview")

    def test_a_preview_run_lands_its_result_on_itself(self):
        from .test_fill_worker import answering_model

        run = self.preview.start(config=quick_config(), row={"company": "acme.com"})
        self._run(run, answering_model(lambda prompt: "found: " + prompt.split()[-1]))
        run.refresh_from_db()
        self.assertEqual(run.status, NodeRunStatus.DONE)
        self.assertEqual(self.preview.result(run).cells["answer"], "found: acme.com")
        # No sheet artifacts, ever: no row, no cell truth.
        self.assertFalse(ListCellState.objects.exists())
        self.assertFalse(ListRow.objects.exists())

    def test_an_exhausted_preview_run_gives_up_on_itself_with_its_cause(self):
        from .test_fill_worker import answering_model

        run = self.preview.start(config=quick_config(), row={"company": "acme.com"})
        prior = CellRunResult(declined_cause=StoredCellState.TRANSIENT)
        NodeRun.objects.filter(id=run.id).update(attempts=NODE_RUN_ATTEMPTS, result=prior.model_dump())
        self._run(run, answering_model(lambda prompt: "never reached"))
        run.refresh_from_db()
        self.assertEqual(run.status, NodeRunStatus.DONE)
        self.assertEqual(self.preview.result(run).declined_cause, StoredCellState.TRANSIENT)
        self.assertFalse(ListCellState.objects.exists())

    def test_an_unreadable_input_settles_unrun_once(self):
        # An input this build cannot read (a config the contract no
        # longer parses, a missing key) settles DONE with no result on
        # its first claim: never parked to the attempt cap with the
        # builder's button busy, never a consumer crash. FAILS if the
        # lane stops catching the read.
        from .test_fill_worker import answering_model

        run = self.preview.start(config=quick_config(), row={"company": "acme.com"})
        NodeRun.objects.filter(id=run.id).update(input={"row": {"company": "acme.com"}})
        self._run(run, answering_model(lambda prompt: "never reached"))
        run.refresh_from_db()
        self.assertEqual((run.status, run.result, run.attempts), (NodeRunStatus.DONE, {}, 1))
        self.assertIsNone(self.preview.result(run))

    def test_the_prune_purges_old_preview_runs_and_only_those(self):
        from openbower_kernel.fields import min_ulid_at

        old = self.preview.start(config=quick_config(), row={"company": "acme.com"})
        old_id = min_ulid_at(timezone.now() - timedelta(seconds=PREVIEW_RUN_MAX_AGE_SECONDS * 2))
        NodeRun.objects.filter(id=old.id).update(id=old_id)
        # An equally old AUTOFILL run must survive: the prune's shape
        # fence (no row, no fill, of the agent kind) is the guard.
        autofill_id = min_ulid_at(timezone.now() - timedelta(seconds=PREVIEW_RUN_MAX_AGE_SECONDS * 3))
        NodeRun.objects.create(
            id=autofill_id,
            account_id=ACCOUNT,
            node_id=old.node_id,
            kind=COLUMN_AGENT,
            row_id="01ROWAAAAAAAAAAAAAAAAAAAAA",
            rank="a0",
            status=NodeRunStatus.DONE,
        )
        keeper = self.preview.start(config=quick_config(), row={"company": "acme.com"})
        self.assertEqual(PrunePreviewRunsOperation().run(), 1)
        self.assertFalse(NodeRun.objects.filter(id=old_id).exists())
        self.assertTrue(NodeRun.objects.filter(id=autofill_id).exists())
        self.assertTrue(NodeRun.objects.filter(id=keeper.id).exists())

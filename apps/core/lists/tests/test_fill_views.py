"""The fill endpoints: add-and-admit, the fills page, cancel, and the
cell-states sidecar, through real cookie auth (the IdP mocked at its
httpx boundary via the shared login helper). model_for is the one
patched seam, per the admission tests' precedent; responses validate
back through the contract models (the parity idiom).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fill_views
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from jobs.constants import JobFailureCode, JobStatus
from jobs.models import Job
from jobs.services import JobRunner
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import ColumnFillSummary, FillRunPage, FillRunWire
from openbower_schema.lists import AiColumn, ListRowsPage

from ..constants import FREE_SEARCH_FILL_BUDGET, JOB_FAILURE_COPY, StoredCellState
from ..models import NodeRun
from ..services import fill_progress
from ..services.lists import ListService
from ..services.node_runs import NodeRunFlow
from .fill_helpers import confirmed_row_count, fill_status, settle, targeted, tick_fill

# Request-shaped config (the serializer derives the output key).
CONFIG = {
    "prompt": "Find the answer for {{company}}",
    "provider": "openai_compatible",
    "source": "ollama",
    "model": "test-model",
    "tools": {},
    "outputs": [{"label": "Answer", "type": "text"}],
}


# A stray error code on a non-failed fill: a state no live writer
# produces, fabricated by two tests to prove the error gates read
# STATUS, not the code's presence.
STRAY_ERROR_CODE = "stray_code"
STRAY_ERROR_MESSAGE = "A code cancel never wrote."


def words(states: dict) -> dict[str, str]:
    """The states map's WORDS, for tests that pin which state a cell
    shows and not the tool statuses beside it."""
    return {key: value.state for key, value in states.items()}


def config_with(output_label: str) -> dict:
    """The same config landing under a different column (the output's
    key derives from its label; the outputs ARE the columns)."""
    return {**CONFIG, "outputs": [{"label": output_label, "type": "text"}]}


def wire_config() -> AgentConfig:
    return AgentConfig(
        prompt=CONFIG["prompt"],
        provider=CONFIG["provider"],
        source=CONFIG["source"],
        model=CONFIG["model"],
        tools=AgentTools(),
        outputs=[AgentOutput(key="answer", label="Answer", type="text")],
    )


def tick_jobs() -> None:
    """Work the walk admission queued: a fill's runs exist once the
    jobs runner has ticked, exactly as they do in production a few
    seconds after the click."""
    JobRunner(worker_id="test:1").tick()


class FillViewsTestCase(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"])
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        patcher = patch("lists.services.fill_admission.base.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_ai(self, list_id: str = "", **overrides):
        body: dict = {"config": CONFIG, "confirmed_row_count": 2}
        body.update(overrides)
        body = {key: value for key, value in body.items() if value is not None}
        resp = self.client.post(
            reverse("lists_columns_ai", kwargs={"id": list_id or str(self.sheet.id)}),
            body,
            content_type="application/json",
        )
        tick_jobs()
        return resp


class AiColumnPostTests(FillViewsTestCase):
    def test_quick_config_admits_201_with_the_run_wire(self) -> None:
        resp = self.post_ai()
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        wire = FillRunWire(**body)
        self.assertEqual(wire.status, "pending")
        self.assertEqual(wire.list_id, str(self.sheet.id))
        self.assertEqual(wire.column_keys, ["answer"])
        self.assertEqual(wire.confirmed_row_count, 2)
        self.assertEqual(wire.started_by, TEST_IDENTITY["id"])
        self.assertIsNone(wire.error)
        # Counters default zeros before the worker writes any.
        self.assertEqual(wire.counters.attempted, 0)
        self.assertEqual(wire.counters.filled, 0)
        self.assertEqual(len(targeted(body["id"])), 2)

    def test_agent_id_path_uses_the_roster_agent(self) -> None:
        agent = AgentService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"], label="Finder", config=wire_config()
        )
        resp = self.post_ai(config=None, agent_id=str(agent.id))
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["agent_id"], str(agent.id))

    def test_config_and_agent_id_are_exclusive(self) -> None:
        agent = AgentService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"], label="Finder", config=wire_config()
        )
        self.assertEqual(self.post_ai(agent_id=str(agent.id)).status_code, 400)
        self.assertEqual(self.post_ai(config=None).status_code, 400)

    def test_same_column_active_fill_is_409_with_the_envelope(self) -> None:
        self.post_ai()
        resp = self.post_ai()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": "fill_active", "detail": "A fill is already running on this column."})

    @override_settings(TOOL_WIRING={"web_search": "duckduckgo"})
    def test_free_search_budget_is_400_with_the_envelope(self) -> None:
        # A request-side refusal, not a conflict: the fix is a narrower
        # ask or metered credentials, and waiting changes nothing.
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        wide = self.lists.create(owner_id=TEST_IDENTITY["id"], label="Wide", columns=[], origin="manual")
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        resp = self.post_ai(
            list_id=str(wide.id),
            config={**CONFIG, "tools": {"web_search": True}},
            confirmed_row_count=rows,
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "free_search_budget")
        self.assertIn("Switch search to a metered vendor", body["detail"])

    def test_an_existing_column_is_400_with_the_envelope(self) -> None:
        # EXISTENCE, not occupancy: this column is empty and still
        # refuses. The old rule asked whether any cell held a value,
        # which is an unindexed scan of the sheet under the List lock
        # to decide something the user can already see.
        self.sheet.columns = [
            *self.sheet.columns,
            {"kind": "plain", "key": "answer", "label": "Answer", "type": "text"},
        ]
        self.sheet.save(update_fields=["columns"])
        resp = self.post_ai()
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "column_collision")
        self.assertIn("already has a answer column", body["detail"])

    def test_foreign_list_and_foreign_agent_read_as_missing(self) -> None:
        foreign_lists = ListService(account_id="01AC" + "Z" * 22)
        foreign_sheet = foreign_lists.create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        foreign_lists.add_rows(foreign_sheet, [{"company": "acme.com"}])
        self.assertEqual(self.post_ai(list_id=str(foreign_sheet.id), confirmed_row_count=1).status_code, 404)
        foreign_agent = AgentService(account_id="01AC" + "Z" * 22).create(
            owner_id=TEST_IDENTITY["id"], label="Theirs", config=wire_config()
        )
        self.assertEqual(self.post_ai(config=None, agent_id=str(foreign_agent.id)).status_code, 404)


class FillsPageTests(FillViewsTestCase):
    def test_keyset_pages_newest_first(self) -> None:
        first = self.post_ai().json()["id"]
        second = self.post_ai(config=config_with("Other")).json()["id"]
        url = reverse("lists_fills", kwargs={"id": str(self.sheet.id)})
        page = self.client.get(url, {"limit": 1}).json()
        wire = FillRunPage(**page)
        self.assertEqual([run.id for run in wire.runs], [second])
        self.assertEqual(wire.next_cursor, second)
        rest = FillRunPage(**self.client.get(url, {"limit": 2, "after": wire.next_cursor}).json())
        self.assertEqual([run.id for run in rest.runs], [first])
        self.assertIsNone(rest.next_cursor)

    def test_foreign_list_is_404(self) -> None:
        foreign = ListService(account_id="01AC" + "Z" * 22).create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        self.assertEqual(self.client.get(reverse("lists_fills", kwargs={"id": str(foreign.id)})).status_code, 404)

    def test_the_poll_query_count_does_not_scale_with_run_count(self) -> None:
        # The poll reads a PAGE of runs' progress in a FIXED number of
        # grouped queries, not derive_counters + derive_heartbeat per run.
        # So the query count for one live run equals the count for three.
        # FAILS if the per-run reads return (the 3+4N walk on a 4s poll).
        url = reverse("lists_fills", kwargs={"id": str(self.sheet.id)})
        self.post_ai()  # one live fill
        self.client.get(url)  # warm any one-time caches so the compare isolates run-count
        with CaptureQueriesContext(connection) as one_run:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.post_ai(config=config_with("Second"))
        self.post_ai(config=config_with("Third"))  # three live fills now
        with CaptureQueriesContext(connection) as three_runs:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(FillRunPage(**resp.json()).runs), 3)  # all three really on the page
        self.assertEqual(len(three_runs.captured_queries), len(one_run.captured_queries))


class FillWireTests(FillViewsTestCase):
    def test_the_heartbeat_is_the_fills_latest_movement_from_its_enqueue(self) -> None:
        # No run yet: the job's own stamp, so a walk still queuing is not
        # a dead worker. Once a run moves later than the job did, the run's.
        fill_run_id = self.post_ai().json()["id"]
        url = reverse("lists_fills", kwargs={"id": str(self.sheet.id)})
        job = Job.objects.get(id=fill_run_id)
        page = FillRunPage(**self.client.get(url).json())
        self.assertEqual(page.runs[0].heartbeat_at, job.last_state_change_at.isoformat())
        tick_fill(fill_run_id)
        later = timezone.now() + timedelta(minutes=5)
        NodeRun.objects.filter(fill_run_id=fill_run_id).update(last_state_change_at=later)
        page = FillRunPage(**self.client.get(url).json())
        self.assertEqual(page.runs[0].heartbeat_at, later.isoformat())

    def test_updated_at_moves_with_the_fills_transitions(self) -> None:
        # Every transition is a queryset update, which auto_now never
        # sees: the wire's updated_at is the last transition's stamp.
        fill_run_id = self.post_ai().json()["id"]
        url = reverse("lists_fills", kwargs={"id": str(self.sheet.id)})
        before = FillRunPage(**self.client.get(url).json()).runs[0].updated_at
        tick_fill(fill_run_id)  # the walk, then the park for its runs
        after = FillRunPage(**self.client.get(url).json()).runs[0].updated_at
        self.assertGreater(after, before)
        self.assertEqual(after, Job.objects.get(id=fill_run_id).last_state_change_at.isoformat())


class ColumnSummaryTests(FillViewsTestCase):
    """The per-column story: the page carries LIVE runs only, and the
    summary carries the newest run's status plus its error when it
    failed, so a terminal run is never re-shipped to say what its
    column already says."""

    def _summaries(self) -> tuple[FillRunPage, dict[str, ColumnFillSummary]]:
        page = FillRunPage(**self.client.get(reverse("lists_fills", kwargs={"id": str(self.sheet.id)})).json())
        return page, {summary.column_key: summary for summary in page.columns}

    def test_the_summaries_never_read_a_settled_fills_runs(self) -> None:
        # Two columns: one whose current fill is settled (its runs still
        # exist), one whose current fill is open. The word is derived
        # from the jobs, and the one question about runs (has any been
        # claimed) is asked of the open fill alone. FAILS if the question
        # names the settled id: that is a finished 50,000-row fill
        # re-read every four seconds forever.
        settled_id = self.post_ai().json()["id"]
        tick_fill(settled_id)  # its runs exist
        Job.objects.filter(id=settled_id).update(status=JobStatus.DONE)
        score = {**CONFIG, "outputs": [{"key": "score", "label": "Score", "type": "text"}]}
        open_id = self.post_ai(config=score).json()["id"]
        # The seam is the pin (captured SQL carries placeholders, not ids).
        with patch.object(NodeRunFlow, "started_fills", wraps=NodeRunFlow.started_fills) as asked:
            _page, by_key = self._summaries()
        self.assertEqual((by_key["answer"].current_fill_id, by_key["answer"].current_status), (settled_id, "complete"))
        self.assertEqual((by_key["score"].current_fill_id, by_key["score"].current_status), (open_id, "pending"))
        asked_ids = [list(call.args[0]) for call in asked.call_args_list]
        self.assertIn([open_id], asked_ids)
        self.assertFalse(any(settled_id in ids for ids in asked_ids))

    def test_a_live_run_rides_the_page_and_names_its_status(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        page, by_key = self._summaries()
        self.assertEqual([run.id for run in page.runs], [fill_run_id])
        self.assertEqual(by_key["answer"].current_status, "pending")
        self.assertIsNone(by_key["answer"].last_error)
        # A never-touched column still ships its summary, ZERO counts
        # included: the summaries build from the sheet's fill columns,
        # never from the grouped cell states, and the client's loading
        # discriminator rests on that (a missing entry on a loaded
        # page is a contract gap, not a fresh column).
        self.assertEqual((by_key["answer"].filled, by_key["answer"].attempted), (0, 0))

    def test_a_failed_run_leaves_the_page_and_lands_its_error(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        fill_progress.fail(fill_run_id, code="model_down", message="The model could not be reached.")
        page, by_key = self._summaries()
        # Live-only, proven from the failing side: the page is EMPTY,
        # so nothing but the summary can be telling this story.
        self.assertEqual(page.runs, [])
        summary = by_key["answer"]
        self.assertEqual(summary.current_status, "failed")
        assert summary.last_error is not None
        self.assertEqual(summary.last_error.code, "model_down")
        self.assertEqual(summary.last_error.message, "The model could not be reached.")

    def test_a_fill_the_runner_failed_reads_its_verdict_under_house_copy(self) -> None:
        # The stored cause is the operator's; the user reads a next step.
        fill_run_id = self.post_ai().json()["id"]
        Job.objects.filter(id=fill_run_id).update(
            status=JobStatus.FAILED, error_code=JobFailureCode.CRASHED, error="RuntimeError: slice exploded"
        )
        _page, by_key = self._summaries()
        summary = by_key["answer"]
        assert summary.last_error is not None
        self.assertEqual(summary.current_status, "failed")
        self.assertEqual(summary.last_error.code, JobFailureCode.CRASHED)
        self.assertEqual(summary.last_error.message, JOB_FAILURE_COPY[JobFailureCode.CRASHED])
        self.assertNotIn("RuntimeError", summary.last_error.message)

    def test_a_newer_clean_run_clears_the_failure(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        fill_progress.fail(fill_run_id, code="model_down", message="The model could not be reached.")
        resp = self.client.post(
            reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}),
            {},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        _, by_key = self._summaries()
        # Reading only the run the column currently names is what
        # clears the failure: nothing is swept.
        self.assertEqual(by_key["answer"].current_status, "pending")
        self.assertIsNone(by_key["answer"].last_error)

    def test_a_stopped_run_carries_its_status_and_no_error(self) -> None:
        # The leg the Continue verb reads, and the pin on "None unless
        # that run FAILED": cancel must never ship an error, however
        # the shared terminal writer's defaults drift.
        fill_run_id = self.post_ai().json()["id"]
        resp = self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        page, by_key = self._summaries()
        self.assertEqual(page.runs, [])
        self.assertEqual(by_key["answer"].current_status, "cancelled")
        self.assertIsNone(by_key["answer"].last_error)
        # The gate is the STATUS, proven from the failing side: a
        # cancelled run wearing a stray code (no live writer produces
        # one; this is the drift the gate exists to survive) still
        # ships no error. This half FAILS under an error_code-only
        # gate, which the happy path above cannot.
        Job.objects.filter(id=fill_run_id).update(error_code=STRAY_ERROR_CODE, error=STRAY_ERROR_MESSAGE)
        _, by_key = self._summaries()
        self.assertIsNone(by_key["answer"].last_error)

    def test_a_column_from_before_the_pointer_reads_as_never_run(self) -> None:
        self.post_ai()
        self.sheet.refresh_from_db()
        # An AI column whose run has not opened: the pointer is blank.
        self.sheet.columns = [
            column.model_copy(update={"current_fill_id": ""}) if isinstance(column, AiColumn) else column
            for column in self.sheet.columns
        ]
        self.sheet.save(update_fields=["columns"])
        _, by_key = self._summaries()
        self.assertEqual(by_key["answer"].current_status, "")
        self.assertIsNone(by_key["answer"].last_error)


class FillCancelTests(FillViewsTestCase):
    def test_cancel_flips_the_run_and_returns_the_wire(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        resp = self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(FillRunWire(**resp.json()).status, "cancelled")
        self.assertEqual(Job.objects.get(id=fill_run_id).status, JobStatus.CANCELLED)

    def test_a_terminal_envelope_ships_its_error_by_status_not_by_code(self) -> None:
        # The envelope's gate is the summary's gate (status FAILED and
        # both legs), proven from the failing side: a cancelled fill
        # wearing a stray code (no live writer produces one) ships no
        # error on its echo. Fails under a code-only gate.
        fill_run_id = self.post_ai().json()["id"]
        url = reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        self.assertEqual(self.client.post(url).status_code, 200)
        Job.objects.filter(id=fill_run_id).update(error_code=STRAY_ERROR_CODE, error=STRAY_ERROR_MESSAGE)
        # Cancel on a terminal fill no-ops and returns the envelope.
        echo = self.client.post(url)
        self.assertEqual(echo.status_code, 200, echo.content)
        wire = FillRunWire(**echo.json())
        self.assertEqual(wire.status, "cancelled")
        self.assertIsNone(wire.error)

    def test_unknown_run_is_404(self) -> None:
        url = reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": "01AA" + "A" * 22})
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_a_run_of_another_sheet_is_not_addressable_here(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        other = self.lists.create(owner_id=TEST_IDENTITY["id"], label="Other sheet", columns=[], origin="manual")
        url = reverse("lists_fill_cancel", kwargs={"id": str(other.id), "fill_run_id": fill_run_id})
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(fill_status(fill_run_id), "pending")


class CellStatesTests(FillViewsTestCase):
    """Cell states ride the ROWS page. Two paged reads walking in
    lockstep was a client-side join carried over the network, and it
    could tear: the values and the states came from two requests."""

    def _states_page(self, **params) -> ListRowsPage:
        url = reverse("lists_rows", kwargs={"id": str(self.sheet.id)})
        resp = self.client.get(url, params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return ListRowsPage(**resp.json())

    def test_pending_blank_filled_and_untouched_columns(self) -> None:
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        page = self._states_page()
        self.assertEqual([item.id for item in page.items], [str(row.id) for row in rows])
        # Freshly admitted: every row pends on the AI column, and the
        # user's own company column never appears.
        self.assertEqual([words(item.states) for item in page.items], [{"answer": "pending"}, {"answer": "pending"}])

        settle(fill_run_id, str(rows[0].id), StoredCellState.NO_EVIDENCE)
        settle(fill_run_id, str(rows[1].id), None)
        page = self._states_page()
        # The diagnosed blank ships its cause; filled is ABSENT (the
        # value in row data is the signal).
        self.assertEqual(words(page.items[0].states), {"answer": "no_evidence"})
        self.assertEqual(words(page.items[1].states), {})

    def test_a_filled_cell_with_a_degraded_tool_ships_its_mark(self) -> None:
        # A clean filled cell is an absence; a filled cell whose run
        # had a degraded tool travels as `filled` WITH the tool
        # statuses, so the value can carry its mark. A blank cell
        # carries its statuses the same way.
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), None, tools={"web_search": "rate_limited", "find_contacts": "open"})
        settle(fill_run_id, str(rows[1].id), StoredCellState.TOOL_UNAVAILABLE, tools={"web_search": "unreachable"})
        page = self._states_page()
        first, second = page.items
        self.assertEqual(first.states["answer"].state, "filled")
        self.assertEqual(first.states["answer"].tools, {"web_search": "rate_limited", "find_contacts": "open"})
        self.assertEqual(second.states["answer"].state, "tool_unavailable")
        self.assertEqual(second.states["answer"].tools, {"web_search": "unreachable"})

    def test_a_filled_cell_with_a_clean_tool_map_stays_an_absence(self) -> None:
        # The live writer's most common shape: filled with every provider
        # OPEN ({"web_search": "open"}), which must suppress exactly
        # like a pre-tools filled row (empty map). FAILS if the
        # degraded test decays to bool(tools), which would put a mark
        # beside every value on every tool-using sheet.
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), None, tools={"web_search": "open", "find_contacts": "open"})
        settle(fill_run_id, str(rows[1].id), None)
        page = self._states_page()
        self.assertEqual([dict(item.states) for item in page.items], [{}, {}])

    def test_a_second_run_re_targets_a_tombstone(self) -> None:
        # A refill targets every blank, settled or not, so a second
        # run covers the old no-answer word with its own pending: the
        # sidecar reads each row's latest outcome ACROSS the column's
        # fills, and the newest fill's queue wins.
        first = self.post_ai().json()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(first["id"], str(rows[0].id), StoredCellState.NO_ANSWER)
        settle(first["id"], str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.client.post(reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": first["id"]}))
        refill = self.client.post(reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}))
        tick_jobs()
        self.assertEqual(refill.status_code, 201, refill.content)
        page = self._states_page()
        # Both rows show the NEW fill's pending, the settled one included.
        self.assertEqual(words(page.items[0].states), {"answer": "pending"})
        self.assertEqual(words(page.items[1].states), {"answer": "pending"})
        # And a newer FILL outranks an older error: the second run
        # fills the retried row, so its old cause must not cover the
        # value (FILLED votes in latest-wins, then drops).
        refill_id = refill.json()["id"]
        settle(refill_id, str(rows[1].id), None)
        page = self._states_page()
        self.assertEqual(words(page.items[1].states), {})

    def test_states_derive_from_each_columns_newest_run(self) -> None:
        # Two fills on two columns: the newest fill overall covers only
        # its own column, so the fill walk must keep going until every
        # fill column has found its newest fill, then stop.
        first = self.post_ai().json()
        second = self.post_ai(config=config_with("Contact")).json()
        first_key = first["column_keys"][0]
        second_key = second["column_keys"][0]
        self.assertNotEqual(first_key, second_key)
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(first["id"], str(rows[0].id), StoredCellState.NO_EVIDENCE)
        page = self._states_page()
        self.assertEqual(words(page.items[0].states), {first_key: "no_evidence", second_key: "pending"})
        self.assertEqual(words(page.items[1].states), {first_key: "pending", second_key: "pending"})

    def test_a_columns_newer_run_supersedes_its_older_ones(self) -> None:
        # Re-running a cancelled column goes through REFILL, not a
        # second column add: the column exists, and adding it again is
        # refused. States then come from the newest fill, and the walk
        # stops without loading the older fill's history.
        stale = self.post_ai().json()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(stale["id"], str(rows[0].id), StoredCellState.NO_EVIDENCE)
        self.client.post(reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": stale["id"]}))
        # The settled blank re-targets once the prompt changes, which
        # is what a user does after cancelling.
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(stale["agent_id"])
        agents.update(agent, config=agent.config().model_copy(update={"prompt": "A sharper ask for {{company}}"}))
        refill = self.client.post(reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}))
        tick_jobs()
        self.assertEqual(refill.status_code, 201, refill.content)
        fresh = refill.json()
        page = self._states_page()
        self.assertEqual(words(page.items[0].states), {"answer": "pending"})
        self.assertEqual(words(page.items[1].states), {"answer": "pending"})
        self.assertEqual(fresh["column_keys"], stale["column_keys"])

    def test_pages_on_the_rows_keyset_by_row_id(self) -> None:
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}])
        self.post_ai(confirmed_row_count=3)
        rows = self.lists.rows_page(self.sheet, limit=10)
        first = self._states_page(limit=2)
        self.assertEqual([item.id for item in first.items], [str(rows[0].id), str(rows[1].id)])
        self.assertEqual(first.next_cursor, f"{rows[1].rank}.{rows[1].id}")
        rest = self._states_page(limit=2, after=first.next_cursor)
        self.assertEqual([item.id for item in rest.items], [str(rows[2].id)])
        self.assertIsNone(rest.next_cursor)

    def test_a_cancelled_runs_unrun_rows_read_as_not_attempted(self) -> None:
        # Stop mid-fill: the untouched rows' outcomes stay PENDING in
        # storage, but a terminal fill's pending is rows it never ran,
        # so the sidecar ships nothing for them (no eternal shimmer);
        # a diagnosed blank from before the stop still ships.
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), StoredCellState.NO_EVIDENCE)
        cancel = self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        )
        self.assertEqual(cancel.status_code, 200)
        page = self._states_page()
        self.assertEqual(words(page.items[0].states), {"answer": "no_evidence"})
        self.assertEqual(words(page.items[1].states), {})

    def test_a_sheet_with_no_fills_ships_empty_states(self) -> None:
        page = self._states_page()
        self.assertEqual([words(item.states) for item in page.items], [{}, {}])

    def test_a_cancelled_refills_untouched_rows_revert_to_their_old_truth(self) -> None:
        # A refill re-targets a retryable row (dot -> shimmer); if that
        # refill stops before running it, the cell is NOT newly blank:
        # the sweep restores the older fill's diagnosis, so the dot
        # comes back instead of the cell going silent.
        first = self.post_ai().json()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(first["id"], str(rows[0].id), StoredCellState.MODEL_ERROR)
        settle(first["id"], str(rows[1].id), None)
        self.client.post(reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": first["id"]}))
        refill = self.client.post(reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}))
        tick_jobs()
        self.assertEqual(refill.status_code, 201, refill.content)
        self.assertEqual(words(self._states_page().items[0].states), {"answer": "pending"})
        self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": refill.json()["id"]})
        )
        self.assertEqual(words(self._states_page().items[0].states), {"answer": "model_error"})


class ListDeleteTests(FillViewsTestCase):
    def test_the_purge_takes_locks_in_the_worker_s_order(self) -> None:
        # The consumer's terminal write is ONE transaction taking the
        # ListRow (write_cells) and then the NodeRun (the task settle).
        # A purge that took them the other way round was an ABBA
        # deadlock against any fill running on this sheet, resolved by
        # Postgres aborting one side: a 500 on the delete, or a burned
        # row attempt.
        #
        # The race itself is unreachable here, because TestCase wraps
        # every test in one transaction, so this pins the ORDER the two
        # tables are written in instead. A reordering that reopens the
        # deadlock fails it.
        self.post_ai()
        seen: list[str] = []
        with CaptureQueriesContext(connection) as ctx:
            self.lists.delete(self.sheet)
        for query in ctx.captured_queries:
            sql = query["sql"]
            if not sql.startswith("DELETE"):
                continue
            for table in ("lists_listrow", "lists_noderun"):
                if table in sql and table not in seen:
                    seen.append(table)
        self.assertEqual(seen, ["lists_listrow", "lists_noderun"])

    def test_deleting_a_sheet_takes_its_columns_ephemeral_agents(self) -> None:
        # An ephemeral agent belongs to one column: it is hidden from
        # the roster and excluded from its cap, so one surviving a
        # deleted sheet is litter no surface can ever show or count.
        fill = self.post_ai().json()
        self.assertTrue(Agent.objects.filter(id=fill["agent_id"], ephemeral=True).exists())
        self.lists.delete(self.sheet)
        self.assertFalse(Agent.objects.filter(id=fill["agent_id"]).exists())

    def test_a_roster_agents_column_never_takes_the_agent_with_it(self) -> None:
        # The same delete must not touch a ROSTER agent a column
        # happened to point at: that one outlives every sheet.
        roster = AgentService(account_id=TEST_IDENTITY["account_id"]).create(
            owner_id=TEST_IDENTITY["id"], label="Finder", config=wire_config()
        )
        self.post_ai(config=None, agent_id=str(roster.id))
        self.lists.delete(self.sheet)
        self.assertTrue(Agent.objects.filter(id=roster.id).exists())


class FillColumnSummaryTests(FillViewsTestCase):
    def test_the_refill_a_column_invites_is_the_one_it_runs(self) -> None:
        # The number the user acts on must not disagree with the fill
        # that acting produces. It is asked on the CONSENT path now and
        # not on the four-second progress poll: it is planning-grade
        # math (a settled-rows query plus an eligibility pass per
        # column) and it has to be exact only where it is spent.
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), StoredCellState.NO_EVIDENCE)
        settle(fill_run_id, str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.client.post(reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id}))
        refill = self.client.post(reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}))
        tick_jobs()
        self.assertEqual(refill.status_code, 201, refill.content)
        # Every blank re-runs, settled or not: both rows.
        self.assertEqual(confirmed_row_count(refill.json()["id"]), 2)

    def test_the_fills_page_carries_per_column_coverage(self) -> None:
        # The tracker renders server truth: `filled` counts the cells
        # in the column that HOLD A VALUE, across every fill (a client
        # sum over one page of fills undercounts the moment history
        # outgrows the page).
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), None)
        resp = self.client.get(reverse("lists_fills", kwargs={"id": str(self.sheet.id)}))
        self.assertEqual(resp.status_code, 200, resp.content)
        page = FillRunPage(**resp.json())
        self.assertEqual(len(page.columns), 1)
        summary = page.columns[0]
        self.assertEqual(summary.column_key, "answer")
        self.assertEqual(summary.current_fill_id, fill_run_id)
        self.assertEqual(summary.filled, 1)

    def test_attempted_is_filled_plus_diagnosed(self) -> None:
        # The denominator that makes `filled` mean something. A
        # targeted cell ends in exactly ONE of two places, a value on
        # the row or a diagnosis saying why not, so their sum is what
        # the column was asked to do. Pairing filled with the SHEET's
        # row count answers a different question and makes a scoped
        # fill read as a failure.
        fill_run_id = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill_run_id, str(rows[0].id), None)
        settle(fill_run_id, str(rows[1].id), StoredCellState.NO_EVIDENCE)
        page = FillRunPage(**self.client.get(reverse("lists_fills", kwargs={"id": str(self.sheet.id)})).json())
        summary = page.columns[0]
        self.assertEqual((summary.filled, summary.attempted), (1, 2))

    def test_answering_a_diagnosed_cell_moves_it_between_the_two_buckets(self) -> None:
        # The invariant `attempted` rests on: a later fill that answers
        # a cell DELETES its diagnosis, so the cell moves from one
        # bucket to the other and the sum stays right. A diagnosis left
        # behind would double-count it forever.
        first = self.post_ai().json()["id"]
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(first, str(rows[0].id), StoredCellState.MODEL_ERROR)
        settle(first, str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.client.post(reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": first}))

        def counts():
            page = FillRunPage(**self.client.get(reverse("lists_fills", kwargs={"id": str(self.sheet.id)})).json())
            return (page.columns[0].filled, page.columns[0].attempted)

        self.assertEqual(counts(), (0, 2))
        refill = self.client.post(reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}))
        tick_jobs()
        self.assertEqual(refill.status_code, 201, refill.content)
        settle(refill.json()["id"], str(rows[0].id), None)
        # One moved buckets; the total is unchanged.
        self.assertEqual(counts(), (1, 2))

    def test_foreign_list_is_404(self) -> None:
        foreign = ListService(account_id="01AC" + "Z" * 22).create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        url = reverse("lists_fills", kwargs={"id": str(foreign.id)})
        self.assertEqual(self.client.get(url).status_code, 404)

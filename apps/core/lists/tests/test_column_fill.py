"""Column fill (POST .../columns/{key}/fill): the one way a fill
starts, from the drawer right after the column is created or from the
column's tracker, through the endpoint with real cookie auth (the IdP mocked at its httpx boundary via the shared login
helper). model_for is the one patched seam, per the fill views'
precedent; responses validate back through the contract models.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_column_fill
"""

from __future__ import annotations

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from jobs.services import JobRunner
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import FillRunWire
from webhooks.services import WebhookDestinationService

from ..constants import StoredCellState
from ..models import ListCellState, NodeRun
from ..services.columns import ColumnService
from ..services.lists import ListService
from ..services.webhook_columns import WebhookColumnService
from ..services.workflows import WorkflowService, agent_id_of
from .fill_helpers import (
    FILLED_VALUE,
    chain_behind,
    created_keys,
    fill_status,
    post_ai_column,
    post_column_fill,
    row_value,
    settle,
    settle_all,
    start_fill,
    target_row_count,
    targeted,
    targeted_numbers,
    targeted_pairs,
    type_cells,
)

# Request-shaped config (the serializer derives the output key).
CONFIG = {
    "prompt": "Find the answer for {{company}}",
    "provider": "openai_compatible",
    "source": "ollama",
    "model": "test-model",
    "tools": {},
    "outputs": [{"label": "Answer", "type": "text"}],
}


def tick_jobs() -> None:
    """Work the walk admission queued: a fill's runs exist once the
    jobs runner has ticked, exactly as they do in production a few
    seconds after the click."""
    JobRunner(worker_id="test:1").tick()


class ColumnFillTestCase(TestCase):
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
        patcher = patch("lists.services.runnable.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_column(self, config: dict = CONFIG) -> list[str]:
        """Create the AI column set; the keys it added, in order."""
        resp = post_ai_column(self.client, str(self.sheet.id), {"config": config})
        self.assertEqual(resp.status_code, 201, resp.content)
        return created_keys(resp.json())

    def add_and_fill(self, config: dict = CONFIG, max_row_count: int = 0) -> dict:
        """The drawer's two requests: create, then fill the first column."""
        keys = self.add_column(config)
        resp = self.fill(key=keys[0], max_row_count=max_row_count)
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def cancel(self, fill_run_id: str) -> None:
        resp = self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def post_fill(self, list_id: str = "", key: str = "answer", max_row_count: int = 0):
        return post_column_fill(self.client, list_id or str(self.sheet.id), key, max_row_count)

    def fill(self, list_id: str = "", key: str = "answer", max_row_count: int = 0):
        """The fill request, then its walk."""
        resp = self.post_fill(list_id, key, max_row_count)
        tick_jobs()
        return resp


class FillTargetTests(ColumnFillTestCase):
    def test_a_fill_runs_only_unattempted_rows(self) -> None:
        # Four rows; the stopped fill answered one (FILLED outcome) and
        # the user typed into another. Both are EXCLUDED: an answered
        # cell is never re-run and never re-billed, and running a
        # user-entered cell would spend on a write-if-blank skip.
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}, {"company": "umbrella.io"}])
        fill = self.add_and_fill()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill["id"], str(rows[0].id), None)
        type_cells(self.sheet, str(rows[2].id), {"answer": "typed by hand"})
        self.cancel(fill["id"])

        resp = self.fill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.status, "pending")
        self.assertEqual(wire.column_keys, ["answer"])
        # Consent facts: the TARGET count, not the sheet total; the
        # cutoff is the current row count.
        self.assertEqual(wire.target_row_count, 4)  # the sheet's rows at the click
        self.assertEqual(wire.counters.attempted, 0)
        # Settled to what the walk found once it ran.
        self.assertEqual(target_row_count(wire.id), 2)
        self.assertEqual(targeted_pairs(wire.id), [(str(rows[1].id), 2), (str(rows[3].id), 4)])

    def test_appended_rows_are_covered(self) -> None:
        # A new fill's cutoff is the current row count, so rows
        # appended after the stopped fill fall inside its target set.
        fill = self.add_and_fill()
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}])

        resp = self.fill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(target_row_count(wire.id), 3)
        self.assertEqual(len(targeted(wire.id)), 3)

    def test_empty_target_refuses_with_the_envelope(self) -> None:
        fill = self.add_and_fill()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle_all(fill["id"], None)
        for row in rows:
            type_cells(self.sheet, str(row.id), {"answer": "done"})
        self.cancel(fill["id"])

        resp = self.fill()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "nothing_to_fill", "detail": "Every row of this column has already been tried."},
        )

    def test_same_column_live_run_is_409(self) -> None:
        self.add_and_fill()
        resp = self.fill()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": "fill_active", "detail": "A fill is already running on this column."})

    def test_a_fill_runs_the_agent_as_edited(self) -> None:
        # A fill reads its agent live, so an edit applies to the next
        # fill (and to a running fill's next row).
        fill = self.add_and_fill()
        self.cancel(fill["id"])
        Agent.objects.filter(id=fill["agent_id"]).update(prompt="Reworded ask for {{company}}")

        resp = self.fill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.agent_id, fill["agent_id"])
        self.assertEqual(Agent.objects.get(id=wire.agent_id).prompt, "Reworded ask for {{company}}")


class StoppedFillTests(ColumnFillTestCase):
    def test_a_stopped_fill_is_over_and_a_new_fill_runs_what_it_never_reached(self) -> None:
        # Stop is final: the fill reads cancelled. A new fill then runs
        # exactly the rows the stopped one never attempted, the row it
        # ran is done whatever came of it, and a row the stop cut the
        # walk short of (never queued at all) is reached the same way.
        # FAILS if the new fill re-runs an attempted row or misses one
        # the stop left untouched.
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}])
        (key,) = self.add_column()
        resp = self.post_fill(key=key)
        self.assertEqual(resp.status_code, 201, resp.content)
        fill = resp.json()
        # One page of two rows walked, then the tick's budget is spent:
        # the third row is never queued.
        with patch("lists.jobs.fill.FILL_SCAN_CHUNK", 2):
            JobRunner(worker_id="test:1").tick(budget_seconds=0)
        rows = self.lists.rows_page(self.sheet, limit=10)
        self.assertEqual(targeted(fill["id"]), {str(rows[0].id), str(rows[1].id)})
        settle(fill["id"], str(rows[0].id), StoredCellState.NO_ANSWER)
        self.cancel(fill["id"])
        self.assertEqual(fill_status(fill["id"]), "cancelled")
        again = self.fill()
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(targeted(again.json()["id"]), {str(rows[1].id), str(rows[2].id)})


class AttemptedRowTests(ColumnFillTestCase):
    def test_an_attempted_row_is_never_re_targeted_verdict_or_failure(self) -> None:
        # A row the fill already ran is done whatever came of it: the
        # model's verdict (no evidence) and an infrastructure failure
        # (model error) alike. Re-asking is the user's explicit gesture,
        # never a later fill's side effect, so with every row attempted the
        # next fill refuses in words that say so. FAILS if an attempted
        # row is re-run.
        fill = self.add_and_fill()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill["id"], str(rows[0].id), StoredCellState.NO_EVIDENCE)
        settle(fill["id"], str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.cancel(fill["id"])
        again = self.fill()
        self.assertEqual(again.status_code, 400, again.content)
        self.assertEqual(
            again.json(), {"error": "nothing_to_fill", "detail": "Every row of this column has already been tried."}
        )

    def _edit_prompt(self, agent_id: str, prompt: str) -> None:
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(agent_id)
        agents.update(agent, config=agent.config().model_copy(update={"prompt": prompt}))

    def test_a_filled_row_is_never_retargeted_even_after_an_edit(self) -> None:
        # An answer is an answer: FILLED is settled regardless of what
        # config produced it.
        fill = self.add_and_fill()
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill["id"], str(rows[0].id), None)
        self.cancel(fill["id"])
        self._edit_prompt(fill["agent_id"], "A sharper ask for {{company}}")
        again = self.fill()
        self.assertEqual(again.status_code, 201, again.content)
        owed = targeted(again.json()["id"])
        self.assertNotIn(str(rows[0].id), owed)
        self.assertIn(str(rows[1].id), owed)


class PartialAnswerTests(ColumnFillTestCase):
    def test_a_row_one_output_answered_is_done_for_every_output(self) -> None:
        # One run answers a row's outputs together: alpha filled, beta
        # declined. Each column carries its own state, and the row was
        # attempted, so a fill on beta, even after the ask changes,
        # owes it nothing (re-asking is the user's gesture). FAILS if a
        # blank sibling re-runs the row.
        two = {**CONFIG, "outputs": [{"label": "Alpha", "type": "text"}, {"label": "Beta", "type": "text"}]}
        fill = self.add_and_fill(two)
        rows = self.lists.rows_page(self.sheet, limit=10)
        settle(fill["id"], str(rows[0].id), causes={"beta": StoredCellState.NO_EVIDENCE})
        settle(fill["id"], str(rows[1].id), None)
        states = {(c.row_id, c.column_key): c.state for c in ListCellState.objects.filter(list_id=str(self.sheet.id))}
        self.assertEqual(states[(str(rows[0].id), "alpha")], StoredCellState.FILLED)
        self.assertEqual(states[(str(rows[0].id), "beta")], StoredCellState.NO_EVIDENCE)
        self.assertEqual(row_value(str(self.sheet.id), str(rows[0].id), "alpha"), FILLED_VALUE)
        self.cancel(fill["id"])
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(fill["agent_id"])
        agents.update(agent, config=agent.config().model_copy(update={"prompt": "A sharper ask for {{company}}"}))
        again = self.fill(key="beta")
        self.assertEqual(again.status_code, 400, again.content)
        self.assertEqual(again.json()["error"], "nothing_to_fill")


class ScopedFillTests(ColumnFillTestCase):
    def test_a_scoped_fill_runs_the_next_tranche(self) -> None:
        # A scoped fill covered rows 1-2; the next scoped fill takes
        # the NEXT eligible unanswered row only, lands its cutoff on
        # that row's number, and leaves row 4 not-attempted (no
        # outcome row at all).
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}, {"company": "umbrella.io"}])
        fill = self.add_and_fill(max_row_count=2)
        first = FillRunWire(**fill)
        self.assertEqual(first.target_row_count, 2)
        settle_all(fill["id"], None)
        self.cancel(fill["id"])

        resp = self.fill(max_row_count=1)
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.target_row_count, 1)
        rows = self.lists.rows_page(self.sheet, limit=10)
        self.assertEqual(targeted_pairs(wire.id), [(str(rows[2].id), 3)])
        self.assertNotIn(4, targeted_numbers(wire.id))

    def test_a_scoped_fill_skips_variable_blank_rows(self) -> None:
        # First N means first N USABLE: the appended variable-blank row
        # never enters the target set, so the scope lands on the
        # eligible row past it.
        fill = self.add_and_fill()
        settle_all(fill["id"], None)
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": ""}, {"company": "initech.com"}])

        resp = self.fill(max_row_count=1)
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.target_row_count, 1)
        self.assertEqual(targeted_numbers(wire.id), [4])

    def test_no_eligible_rows_refuses_with_the_envelope(self) -> None:
        # Unanswered rows remain, but the prompt cannot act on any of
        # them: a distinct refusal from nothing_to_fill, since the fix is
        # filling in values, not accepting a finished column.
        fill = self.add_and_fill()
        settle_all(fill["id"], None)
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": ""}])

        resp = self.fill()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "no_eligible_rows", "detail": "No rows have values for this prompt's variables."},
        )


class FillNotFoundTests(ColumnFillTestCase):
    def test_foreign_list_reads_as_missing(self) -> None:
        foreign_account = "01AC" + "Z" * 22
        foreign_user = "01US" + "Z" * 22
        foreign_lists = ListService(account_id=foreign_account)
        foreign_sheet = foreign_lists.create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        foreign_lists.add_rows(foreign_sheet, [{"company": "acme.com"}])
        start_fill(
            str(foreign_sheet.id),
            account_id=foreign_account,
            user_id=foreign_user,
            config=AgentConfig(
                prompt=CONFIG["prompt"],
                provider=CONFIG["provider"],
                source=CONFIG["source"],
                model=CONFIG["model"],
                tools=AgentTools(),
                outputs=[AgentOutput(key="answer", label="Answer", type="text")],
            ),
        )
        self.assertEqual(self.fill(list_id=str(foreign_sheet.id)).status_code, 404)

    def test_a_column_without_a_fill_is_404(self) -> None:
        # "company" exists but carries no fill member; "missing" does
        # not exist at all. Both read as not-found, not refusals.
        self.assertEqual(self.fill(key="company").status_code, 404)
        self.assertEqual(self.fill(key="missing").status_code, 404)


class FillLifecycleTests(ColumnFillTestCase):
    def test_fills_on_a_column_serialize_through_the_409(self) -> None:
        # Fills on a column accumulate over time but never overlap.
        fill = self.add_and_fill()
        self.cancel(fill["id"])
        first = self.fill()
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(FillRunWire(**first.json()).status, "pending")
        second = self.fill()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"], "fill_active")


class AgentColumnsTests(ColumnFillTestCase):
    """A fill writes its agent's columns as one unit and never adds one:
    the columns were made by the create, and an agent's output set is
    fixed while they exist."""

    TWO = {**CONFIG, "outputs": [{"label": "Alpha", "type": "text"}, {"label": "Beta", "type": "text"}]}

    def _pointers(self) -> dict[str, str]:
        self.sheet.refresh_from_db()
        return {column.key: column.current_fill_id for column in self.sheet.columns if column.kind == "ai"}

    def test_a_fill_points_every_column_of_its_agent_at_itself(self) -> None:
        # Started from either column, the fill owns both and both name
        # it. FAILS if the fill writes or points at fewer than the
        # agent's columns.
        (alpha, beta) = self.add_column(self.TWO)
        resp = self.post_fill(key=beta)
        self.assertEqual(resp.status_code, 201, resp.content)
        fill = resp.json()
        self.assertEqual(fill["column_keys"], [alpha, beta])
        self.assertEqual(self._pointers(), {alpha: fill["id"], beta: fill["id"]})

    def test_a_column_no_output_backs_is_skipped_or_refused(self) -> None:
        # Reachable only when an agent save raced the create (a save
        # refuses output changes while columns exist), so it is forced
        # here under the guard: the sibling no output backs is left
        # unwritten, and asking for it by name refuses.
        (alpha, beta) = self.add_column(self.TWO)
        self.sheet.refresh_from_db()
        node_id = next(column.node_id for column in self.sheet.columns if column.key == alpha)
        agent_id = agent_id_of(WorkflowService(account_id=TEST_IDENTITY["account_id"]).get_node(node_id))
        Agent.objects.filter(id=agent_id).update(outputs=[{"key": alpha, "label": "Alpha", "type": "text"}])
        refused = self.post_fill(key=beta)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["error"], "fill_column_retired")
        resp = self.post_fill(key=alpha)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["column_keys"], [alpha])
        self.assertEqual(self._pointers(), {alpha: resp.json()["id"], beta: ""})

    def test_a_fill_never_adds_a_column(self) -> None:
        # An output with no column on the sheet (one of the agent's two
        # columns deleted) is not added back by a fill: the consent
        # names the surviving column only and the sheet's keys are
        # unchanged. FAILS if the fill claims a column for every output.
        (alpha, beta) = self.add_column(self.TWO)
        ColumnService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"]).delete(
            str(self.sheet.id), key=beta
        )
        resp = self.post_fill(key=alpha)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["column_keys"], [alpha])
        self.sheet.refresh_from_db()
        self.assertEqual([column.key for column in self.sheet.columns], ["company", alpha])

    def test_a_send_webhook_columns_fill_is_not_found(self) -> None:
        # Its barrier fills a Send column; no fill starts there. Built
        # through the real service so the refusal is the gate's own,
        # never a missing node.
        self.add_column()
        destinations = WebhookDestinationService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})
        WebhookColumnService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"]).add(
            str(self.sheet.id),
            label="CRM sync",
            destination_id=str(destination.id),
            wait_keys=["answer"],
            payload_keys=["company"],
            interval_seconds=3600,
        )
        resp = self.post_fill(key="crm_sync")
        self.assertEqual(resp.status_code, 404, resp.content)


class EntryActionColumnTests(ColumnFillTestCase):
    """The sheet names the nodes a fill may start at, and the fill
    request holds the same line."""

    def _node_of(self, key: str) -> str:
        self.sheet.refresh_from_db()
        return next(column.node_id for column in self.sheet.columns if column.key == key)

    def test_the_detail_names_the_entry_actions(self) -> None:
        # The create's echo and the detail read both carry the entry
        # actions, so the sheet knows which columns offer a fill without
        # asking. FAILS if a detail drops the set or counts a chained node.
        resp = self.client.post(
            reverse("lists_columns_ai", kwargs={"id": str(self.sheet.id)}),
            {"config": CONFIG},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        answer = self._node_of("answer")
        self.assertEqual(resp.json()["entry_action_ids"], [answer])
        chain_behind(self.sheet, upstream_node_id=answer, key="later")
        detail = self.client.get(reverse("lists_detail", kwargs={"id": str(self.sheet.id)}))
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["entry_action_ids"], [answer])

    def test_every_column_write_answers_with_the_entry_actions(self) -> None:
        # The sheet replaces its state from every column write's echo, so
        # each carries the entry actions; one answering a bare summary
        # would drop the tracker's Fill controls. FAILS if any column
        # write answers a summary.
        self.add_column()
        answer = self._node_of("answer")
        destinations = WebhookDestinationService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})
        list_id = str(self.sheet.id)
        detail_url = reverse("lists_detail", kwargs={"id": list_id})
        column_url = reverse("lists_column_detail", kwargs={"id": list_id, "key": "notes"})
        json = "application/json"

        def reorder():
            keys = [column["key"] for column in self.client.get(detail_url).json()["columns"]]
            keys.reverse()
            url = reverse("lists_columns_order", kwargs={"id": list_id})
            return self.client.patch(url, {"keys": keys}, content_type=json)

        webhook = {
            "label": "CRM sync",
            "destination_id": str(destination.id),
            "wait_keys": ["answer"],
            "payload_keys": ["company"],
            "interval_seconds": 3600,
        }
        writes = [
            ("list patch", lambda: self.client.patch(detail_url, {"label": "Renamed"}, content_type=json)),
            (
                "column add",
                lambda: self.client.post(
                    reverse("lists_columns", kwargs={"id": list_id}),
                    {"label": "Notes", "type": "text"},
                    content_type=json,
                ),
            ),
            ("column rename", lambda: self.client.patch(column_url, {"label": "Memo"}, content_type=json)),
            ("column order", reorder),
            (
                "webhook add",
                lambda: self.client.post(
                    reverse("lists_columns_webhook", kwargs={"id": list_id}), webhook, content_type=json
                ),
            ),
            ("column delete", lambda: self.client.delete(column_url)),
        ]
        for name, write in writes:
            with self.subTest(write=name):
                resp = write()
                self.assertIn(resp.status_code, (200, 201), resp.content)
                self.assertEqual(resp.json()["entry_action_ids"], [answer])

    def test_a_column_behind_a_barrier_is_400_with_the_envelope(self) -> None:
        self.add_column()
        chain_behind(self.sheet, upstream_node_id=self._node_of("answer"), key="later")
        resp = self.fill(key="later")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(
            resp.json(),
            {
                "error": "fill_column_downstream",
                "detail": "This column fills after the columns it waits on. Fill those columns instead.",
            },
        )

    def test_the_index_reads_no_workflow(self) -> None:
        # The entry actions are the open sheet's: the index (also the
        # machine lane's "which lists can I push to") carries no node
        # ids and reads no node. FAILS if the index summaries grow them.
        self.add_column()
        with CaptureQueriesContext(connection) as captured:
            resp = self.client.get(reverse("lists_index"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse([q["sql"] for q in captured.captured_queries if '"lists_node"' in q["sql"]])
        items = {item["id"]: item for item in resp.json()["items"]}
        self.assertNotIn("entry_action_ids", items[str(self.sheet.id)])


class ChildAccountTests(ColumnFillTestCase):
    """Every writer must stamp the account. A missed one stores "" in
    silence (CharField's empty is its implicit default), so nothing
    but an explicit assertion catches it."""

    def test_every_fill_child_carries_its_account(self):
        account = TEST_IDENTITY["account_id"]
        fill = self.add_and_fill()
        settle_all(fill["id"], StoredCellState.NO_EVIDENCE)
        outcomes = NodeRun.objects.filter(fill_run_id=fill["id"])
        cells = ListCellState.objects.filter(list_id=str(self.sheet.id))
        self.assertTrue(outcomes.exists() and cells.exists())
        self.assertEqual({o.account_id for o in outcomes}, {account})
        self.assertEqual({c.account_id for c in cells}, {account})

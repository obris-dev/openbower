"""Admission: the one transaction. Real DB and services; model_for is
the one patched seam (the framework's wire is not ours to test), per
the runtime tests' precedent."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings

from agents.models import Agent
from agents.providers import ModelUnavailable
from jobs.constants import JobStatus
from jobs.models import Job
from jobs.services import JobRunner, JobService
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig, AgentOutput, AgentTools
from openbower_schema.lists import AiColumn

from ..constants import (
    FREE_SEARCH_FILL_BUDGET,
    MAX_ACTIVE_FILLS,
    MAX_LIST_COLUMNS,
    CellSource,
)
from ..jobs.rerank import Rerank
from ..models import Node, NodeRun
from ..services import fill_progress
from ..services.fill_admission import (
    AccountFillsFull,
    ColumnCollision,
    ColumnsFull,
    DerivedKeyCollision,
    EmptyFill,
    FillAdmissionService,
    FreeSearchBudget,
    ModelUnrunnable,
    NoEligibleRows,
    ProviderRetiredRefusal,
    SameColumnFillActive,
)
from ..services.fills import FillService
from ..services.lists import ListService
from .fill_helpers import confirmed_row_count, consent_of, fill_status, targeted, targeted_numbers

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


def tick_jobs() -> None:
    """Work the walk admission queued: a fill's runs exist once the
    jobs runner has ticked, exactly as they do in production a few
    seconds after the click."""
    JobRunner(worker_id="test:1").tick()


class AdmissionTestCase(TestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT)
        self.admission = FillAdmissionService(account_id=ACCOUNT, user_id=USER)
        self.fills = FillService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        patcher = patch("lists.services.runnable.model_for")
        self.model_for = patcher.start()
        self.addCleanup(patcher.stop)

    def admit(self, **overrides):
        kwargs = {
            "list_id": str(self.sheet.id),
            "config": quick_config(),
            "confirmed_row_count": 2,
        }
        kwargs.update(overrides)
        fill = self.admission.admit(**kwargs)
        tick_jobs()
        fill.refresh_from_db()
        return fill


class QuickPathTests(AdmissionTestCase):
    def test_admit_creates_ephemeral_column_run_and_queue(self) -> None:
        fill = self.admit()
        agent = Agent.objects.get(id=consent_of(str(fill.id)).agent_id)
        self.assertTrue(agent.ephemeral)
        # The ephemeral row's label is the FIRST output's.
        self.assertEqual(agent.label, "Answer")
        self.sheet.refresh_from_db()
        added = [c for c in self.sheet.columns if c.key == "answer"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].label, "Answer")
        # The column carries BOTH custody facts: which node fills it
        # (the agent bound to this sheet) and which fill currently
        # speaks for it (stored, not walked).
        node = Node.objects.get(identity=str(agent.id))
        self.assertEqual(
            (added[0].kind, added[0].node_id, added[0].current_fill_id), ("ai", str(node.id), str(fill.id))
        )
        self.assertEqual(fill_status(str(fill.id)), "pending")
        self.assertEqual(consent_of(str(fill.id)).column_keys, ["answer"])
        # A fill reads its agent live: the config lives on the agent,
        # not frozen on the fill.
        self.assertEqual(agent.config().model, "test-model")
        self.assertEqual(len(targeted(str(fill.id))), 2)
        # Fill-backed tasks denormalize their list off the fill job, and
        # every one is a run of the column's node from birth.
        tasks = NodeRun.objects.filter(fill_run_id=str(fill.id))
        self.assertEqual({t.list_id for t in tasks}, {str(self.sheet.id)})
        self.assertEqual({t.node_id for t in tasks}, {str(node.id)})

    def test_the_stored_ai_column_lands_at_rest_as_the_wire_shape(self) -> None:
        # Read raw: what admission wrote to the jsonb is the AI member's
        # dump, key for key, so no wire key goes unwritten.
        self.admit()
        with connection.cursor() as cursor:
            cursor.execute("SELECT columns FROM lists_list WHERE id = %s", [str(self.sheet.id)])
            [(raw,)] = cursor.fetchall()
        stored = json.loads(raw) if isinstance(raw, str) else raw
        [answer] = [column for column in stored if column["key"] == "answer"]
        self.assertEqual(answer["kind"], "ai")
        self.assertEqual(set(answer), set(AiColumn.model_fields))

    def test_multi_output_columns_are_the_outputs_own_keys(self) -> None:
        config = quick_config(
            outputs=[
                AgentOutput(key="email", label="Email", type="email"),
                AgentOutput(key="status", label="Status", type="text"),
            ]
        )
        fill = self.admit(config=config)
        self.assertEqual(consent_of(str(fill.id)).column_keys, ["email", "status"])
        self.sheet.refresh_from_db()
        keys = {c.key for c in self.sheet.columns}
        self.assertIn("email", keys)
        self.assertIn("status", keys)
        labels = {c.key: c.label for c in self.sheet.columns}
        self.assertEqual(labels["email"], "Email")
        self.assertEqual(labels["status"], "Status")

    def test_an_existing_column_refuses_whatever_is_in_it(self) -> None:
        # EXISTENCE is the rule, not occupancy. Asking whether a column
        # held any value meant scanning the sheet with no index behind
        # it, under the List lock, to decide something the user can see
        # for themselves: the column is there.
        self.sheet.columns = [
            *self.sheet.columns,
            {"kind": "plain", "key": "answer", "label": "Answer", "type": "text"},
        ]
        self.sheet.save(update_fields=["columns"])
        with self.assertRaises(ColumnCollision) as caught:
            self.admit()
        self.assertIn("already has a answer column", str(caught.exception))

    def test_a_column_an_agent_fills_names_the_better_next_step(self) -> None:
        # Deleting is the wrong advice for a column an agent already
        # fills: re-running it from the column itself is right there,
        # and the answers under it are the user's.
        fill = self.admit()
        FillService(account_id=ACCOUNT).cancel(str(fill.id))
        self.sheet.refresh_from_db()
        with self.assertRaises(ColumnCollision) as caught:
            self.admit()
        self.assertIn("Fill remaining", str(caught.exception))

    def test_occupied_collision_refuses(self) -> None:
        self.sheet.columns = [
            *self.sheet.columns,
            {"kind": "plain", "key": "answer", "label": "Answer", "type": "text"},
        ]
        self.sheet.save(update_fields=["columns"])
        rows = self.lists.rows_page(self.sheet, limit=1)
        self.lists.write_cells(
            str(self.sheet.id),
            str(rows[0].id),
            {"answer": "taken"},
            column_keys=("answer",),
            source=CellSource.MANUAL,
            fill_run_id=None,
            declined_cause=None,
            tools={},
        )
        with self.assertRaises(ColumnCollision):
            self.admit()
        # Nothing committed: no fill, no ephemeral, no columns change.
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        self.assertEqual(Agent.objects.count(), 0)

    def test_duplicate_output_keys_refuse(self) -> None:
        # The request serializer refuses duplicate keys at the provider,
        # but AgentConfig itself does not: a config arriving any other
        # way with two same-key outputs would silently merge one
        # output's answers into the other's column.
        config = quick_config(
            outputs=[
                AgentOutput(key="email", label="Email", type="email"),
                AgentOutput(key="email", label="Backup email", type="email"),
            ]
        )
        with self.assertRaises(DerivedKeyCollision) as caught:
            self.admit(config=config)
        # The copy names both outputs and the fix.
        self.assertIn("Email", str(caught.exception))
        self.assertIn("Backup email", str(caught.exception))
        self.assertIn("rename one", str(caught.exception))
        # Nothing committed: no fill, no ephemeral, no columns change.
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        self.sheet.refresh_from_db()
        self.assertEqual([c.key for c in self.sheet.columns], ["company"])

    def test_list_delete_purges_runs_and_outcomes(self) -> None:
        # No cascades exist: delete() owns the fill custody's cleanup,
        # or a live orphaned fill holds an account fill slot forever
        # with nothing visible to cancel.
        self.admit()
        # A job of another kind on the same list goes with it too.
        JobService(account_id=ACCOUNT).enqueue_system(Rerank(list_id=str(self.sheet.id)), target_id=str(self.sheet.id))
        self.lists.delete(self.sheet)
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        self.assertEqual(Job.objects.filter(target_id=str(self.sheet.id)).count(), 0)
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_the_consent_range_is_the_echoed_count_never_the_grown_sheet(self) -> None:
        # The user reviewed 1 row; a second landed before the click. The
        # fill covers exactly what was reviewed, and the newcomer shows
        # unfilled for the next refill: no refusal, no surprise spend.
        fill = self.admit(confirmed_row_count=1)
        self.assertEqual((confirmed_row_count(str(fill.id)), targeted_numbers(str(fill.id))), (1, [1]))

    def test_the_probe_stops_where_the_walk_would(self) -> None:
        # The only row the prompt can act on sits past the count the
        # user was shown: the walk would never reach it, so admission
        # refuses rather than opening a fill that targets nothing.
        sheet = self.lists.create(
            owner_id=USER,
            label="Lagging",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(sheet, [{}, {"company": "acme.com"}])
        with self.assertRaises(NoEligibleRows):
            self.admit(list_id=str(sheet.id), confirmed_row_count=1)
        fill = self.admit(list_id=str(sheet.id), confirmed_row_count=2)
        self.assertEqual(targeted_numbers(str(fill.id)), [2])

    def test_a_shrunken_sheet_admits_and_fills_less(self) -> None:
        # Reviewed 99, the sheet has 2: fewer rows than consented is
        # cheaper, never a betrayal; the denominator settles to what
        # the walk found.
        fill = self.admit(confirmed_row_count=99)
        self.assertEqual(confirmed_row_count(str(fill.id)), 2)

    def test_empty_sheet_refuses(self) -> None:
        empty = self.lists.create(owner_id=USER, label="Empty", columns=[], origin="manual")
        with self.assertRaises(EmptyFill):
            self.admit(list_id=str(empty.id), confirmed_row_count=0)

    def test_model_check_refuses_unrunnable(self) -> None:
        self.model_for.side_effect = ModelUnavailable("no such model on this deploy")
        with self.assertRaises(ModelUnrunnable):
            self.admit()


class GuardTests(AdmissionTestCase):
    def test_same_column_live_run_refuses(self) -> None:
        self.admit()
        # A second sheet column would collide with the first fill's
        # target key while it is still live.
        with self.assertRaises(SameColumnFillActive):
            self.admit(confirmed_row_count=2)

    def test_the_cap_counts_fills_only(self) -> None:
        # The fills share one table with the other kinds: open system
        # jobs on the account (a re-space, a backfill) are not fills and
        # take no slot. FAILS if the count loses its kind filter.
        for _ in range(MAX_ACTIVE_FILLS):
            JobService(account_id=ACCOUNT).enqueue_system(
                Rerank(list_id=str(self.sheet.id)), target_id=str(self.sheet.id)
            )
        self.admit()

    def test_account_cap_refuses(self) -> None:
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        with self.assertRaises(AccountFillsFull):
            self.admit()

    def test_a_finished_fill_frees_its_account_slot(self) -> None:
        # The cap counts LIVE fills only (the locked count path).
        fills = []
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            fills.append(self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1))
        Job.objects.filter(id=fills[0].id).update(status=JobStatus.DONE)
        fill = self.admit()
        self.assertEqual(fill_status(str(fill.id)), "pending")

    @override_settings(TOOL_WIRING={"web_search": "duckduckgo"})
    def test_free_search_budget_refuses_wide_tool_fills(self) -> None:
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget) as caught:
            self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)
        # Rendered verbatim in the drawer: user words only (no internal
        # provider vocabulary), and the next step is the paid provider.
        self.assertEqual(
            str(caught.exception),
            f"This fill could need up to {rows * MAX_TOOL_CALLS:,} searches; free search is budgeted for "
            f"{FREE_SEARCH_FILL_BUDGET} per fill. Switch search to a metered vendor (a deployment setting) for"
            " metered search.",
        )

    @override_settings(
        TOOL_WIRING={"web_search": "serper"},
        TOOL_VENDOR_KEYS={"serper": {"api_key": "secret"}},
    )
    def test_paid_provider_lifts_the_free_budget(self) -> None:
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        fill = self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)
        self.assertEqual(fill_status(str(fill.id)), "pending")

    @override_settings(TOOL_VENDOR_KEYS={"serper": {"api_key": "secret"}})
    def test_a_contacts_only_fill_is_never_free_budgeted(self) -> None:
        # Contact search is metered whatever the switch says (it pins
        # the paid provider), so a contacts-only fill spends nothing
        # free and the free budget must not cap it. FAILS if the gate
        # reads uses_tools instead of searches_web.
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(find_contacts=True))
        fill = self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)
        self.assertEqual(fill_status(str(fill.id)), "pending")

    @override_settings(
        TOOL_WIRING={"web_search": "duckduckgo"},
        TOOL_VENDOR_KEYS={"serper": {"api_key": "secret"}},
    )
    def test_credentials_alone_do_not_lift_the_free_budget(self) -> None:
        # Credentials route nothing: web search runs its WIRED vendor
        # (contact search runs its own metered roster regardless), so
        # with the wiring on the free vendor the budget must still
        # refuse.
        # FAILS if the predicate reads the credential pair again.
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget):
            self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)


class ScopedFillTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Positions 3-5: a variable-blank row INSIDE the scope range,
        # then two more eligible rows (1-2 seeded by the base class).
        self.lists.add_rows(self.sheet, [{"company": ""}, {"company": "initech.com"}, {"company": "umbrella.io"}])

    def test_scoped_admit_targets_the_first_n_eligible(self) -> None:
        # max_row_count=3 walks rows 1, 2, then SKIPS the variable-blank
        # row at 3 (it would render an empty ask) and takes row 4:
        # first N means first N usable.
        fill = self.admit(max_row_count=3, confirmed_row_count=5)
        self.assertEqual(confirmed_row_count(str(fill.id)), 3)
        # The cutoff is the LAST TARGETED row, not the sheet size.
        self.assertEqual(targeted_numbers(str(fill.id)), [1, 2, 4])

    def test_rows_past_the_cutoff_have_no_outcome_rows(self) -> None:
        # Not-attempted is the ABSENCE of an outcome row: row 5
        # (past the scoped cutoff) and row 3 (ineligible) never
        # materialize.
        fill = self.admit(max_row_count=3, confirmed_row_count=5)
        self.assertEqual(len(targeted_numbers(str(fill.id))), 3)
        self.assertNotIn(3, targeted_numbers(str(fill.id)))
        self.assertNotIn(5, targeted_numbers(str(fill.id)))

    def test_a_scoped_admit_ranges_over_the_sheet_and_stops_at_n(self) -> None:
        # A scoped fill asked for the first N usable rows; the sheet
        # total was never the number it consented to, so the range is
        # the sheet as it stands and N is the ceiling.
        fill = self.admit(max_row_count=1, confirmed_row_count=1)
        self.assertEqual(confirmed_row_count(str(fill.id)), 1)

    def test_no_eligible_rows_refuses(self) -> None:
        bare = self.lists.create(
            owner_id=USER,
            label="Bare",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(bare, [{"company": ""}, {"other": "unrelated"}])
        with self.assertRaises(NoEligibleRows) as caught:
            self.admission.admit(list_id=str(bare.id), config=quick_config(), confirmed_row_count=2)
        self.assertEqual(str(caught.exception), "No rows have values for this prompt's variables.")
        # Nothing committed: no fill, no ephemeral, no columns change.
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        self.assertEqual(Agent.objects.count(), 0)
        bare.refresh_from_db()
        self.assertEqual([c.key for c in bare.columns], ["company"])

    def test_variable_less_prompt_treats_every_row_as_eligible(self) -> None:
        # No {{tokens}} means the prompt asks the same question
        # everywhere; the variable-blank row is a target like any other.
        fill = self.admit(config=quick_config(prompt="Name three colors."), confirmed_row_count=5)
        self.assertEqual(confirmed_row_count(str(fill.id)), 5)
        self.assertEqual(len(targeted(str(fill.id))), 5)

    @override_settings(TOOL_WIRING={"web_search": "duckduckgo"})
    def test_scope_bounds_the_free_search_budget(self) -> None:
        # The budget reads the TARGET count: a scoped fill on a sheet
        # too wide to run free still admits when N fits the budget.
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        over = FREE_SEARCH_FILL_BUDGET // 4 + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(over)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget):
            self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=over)
        fill = self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=over, max_row_count=4)
        self.assertEqual(fill_status(str(fill.id)), "pending")
        self.assertEqual(confirmed_row_count(str(fill.id)), 4)


class RosterPathTests(AdmissionTestCase):
    def test_roster_agent_is_used_not_duplicated(self) -> None:
        agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())
        fill = self.admit(config=None, agent_id=str(agent.id))
        self.assertEqual(consent_of(str(fill.id)).agent_id, str(agent.id))
        self.assertEqual(Agent.objects.count(), 1)

    def test_retired_provider_refuses(self) -> None:
        agent = self.admission.agents.create(owner_id=USER, label="Old", config=quick_config())
        Agent.objects.filter(id=agent.id).update(provider="legacy_provider")
        with self.assertRaises(ProviderRetiredRefusal):
            self.admit(config=None, agent_id=str(agent.id))


class AdmissionLockSpanTests(AdmissionTestCase):
    """Where admission takes the List row lock, and for how long.

    The lock exists for ONE thing, the columns array write, and the
    expensive part of admission (a NodeRun per targeted row) must not
    happen while it is held: that same lock is taken by every column
    add, rename, reorder and delete, by add_rows, by the list delete,
    and by another admission, so a large fill holding it stalls all of
    them.

    A Postgres row lock cannot be released early, so the only way to
    hold it briefly is to take it LATE."""

    def sql(self, captured) -> list[str]:
        return [q["sql"].lstrip().upper() for q in captured.captured_queries]

    def index_of(self, sql: list[str], match) -> int:
        """First statement matching, as a FAILURE rather than a bare
        StopIteration when a marker disappears from the query stream."""
        index = next((i for i, statement in enumerate(sql) if match(statement)), None)
        self.assertIsNotNone(index, "expected statement not found in the captured queries")
        return index

    def test_the_request_inserts_no_runs_and_locks_the_list_around_one_write(self) -> None:
        # The walk is the job's: the request queues it and writes the
        # column under the lock, nothing else. A 50,000 row consent
        # costs the request one probe page.
        with CaptureQueriesContext(connection) as captured:
            self.admission.admit(list_id=str(self.sheet.id), config=quick_config(), confirmed_row_count=2)
        sql = self.sql(captured)
        self.assertEqual([s for s in sql if s.startswith('INSERT INTO "LISTS_NODERUN"')], [])
        self.assertEqual(len([s for s in sql if s.startswith('INSERT INTO "JOBS_JOB"')]), 1)
        self.index_of(sql, lambda s: '"LISTS_LIST"' in s and "FOR UPDATE" in s)
        self.assertEqual(len([s for s in sql if s.startswith('UPDATE "LISTS_LIST"')]), 1)

    def test_deterministic_refusals_never_queue_the_walk(self) -> None:
        # An account at its cap and a sheet at its column cap are both
        # knowable before any work: hearing the "no" after inserting up
        # to 50,000 NodeRun rows would waste the build EVERY time, not
        # on a race.
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        queued_before = Job.objects.count()
        with CaptureQueriesContext(connection) as captured, self.assertRaises(AccountFillsFull):
            self.admit()
        inserts = [s for s in self.sql(captured) if s.startswith('INSERT INTO "JOBS_JOB"')]
        self.assertEqual(inserts, [], "the cap was knowable before the walk was queued")
        self.assertEqual(Job.objects.count(), queued_before)

    def test_at_both_caps_the_fill_cap_wins(self) -> None:
        # fills_full is a 409 whose fix is waiting; columns_full is a
        # 400 whose fix is changing the sheet. An account at both must
        # hear the one that waiting actually fixes, the same precedence
        # the locked claim keeps.
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        wide = self.lists.create(
            owner_id=USER,
            label="Wide",
            columns=[
                {"kind": "plain", "key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)
            ],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        with self.assertRaises(AccountFillsFull):
            self.admission.admit(list_id=str(wide.id), config=quick_config(), confirmed_row_count=1)

    def test_a_full_sheet_refuses_before_queuing_the_walk(self) -> None:
        wide = self.lists.create(
            owner_id=USER,
            label="Wide",
            columns=[
                {"kind": "plain", "key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)
            ],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        with CaptureQueriesContext(connection) as captured, self.assertRaises(ColumnsFull):
            self.admission.admit(list_id=str(wide.id), config=quick_config(), confirmed_row_count=1)
        inserts = [s for s in self.sql(captured) if s.startswith('INSERT INTO "JOBS_JOB"')]
        self.assertEqual(inserts, [], "the column cap was knowable before the walk was queued")

    def test_the_columns_array_is_written_ONCE(self) -> None:
        # It used to be written twice: the append, then a separate
        # current_fill_id stamp, with the queue insert in between.
        # Those two writes are what forced the lock to span the insert.
        with CaptureQueriesContext(connection) as captured:
            self.admit()
        writes = [s for s in self.sql(captured) if s.startswith('UPDATE "LISTS_LIST"')]
        self.assertEqual(len(writes), 1, writes)

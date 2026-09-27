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
)
from ..jobs.rerank import RerankJob
from ..models import Node, NodeRun
from ..nodes.column_agent import ColumnAgent
from ..nodes.entry import Entry
from ..services import fill_progress
from ..services.ai_columns import AiColumnService
from ..services.columns import ColumnExists, ColumnService
from ..services.fill_admission import (
    AccountFillsFull,
    ColumnCollision,
    ColumnsFull,
    DerivedKeyCollision,
    EmptyFill,
    FillAdmissionService,
    FillColumnDownstream,
    FreeSearchBudget,
    ModelUnrunnable,
    NoEligibleRows,
    ProviderRetiredRefusal,
    SameColumnFillActive,
)
from ..services.fills import FillService
from ..services.lists import ListService
from ..services.workflows import WorkflowService
from .fill_helpers import (
    chain_behind,
    consent_of,
    fill_status,
    start_fill,
    target_row_count,
    targeted,
    targeted_numbers,
    type_cells,
)

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
        self.ai_columns = AiColumnService(account_id=ACCOUNT, user_id=USER)
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

    def add_column(self, *, list_id: str = "", config: AgentConfig | None = None, agent_id: str = "") -> list[str]:
        """The AI column create alone: the keys its outputs made, in order."""
        target_id = list_id or str(self.sheet.id)
        before = {column.key for column in self.lists.get(target_id).columns}
        if config is None and not agent_id:
            config = quick_config()
        added = self.ai_columns.add(target_id, config=config, agent_id=agent_id)
        return [column.key for column in added.columns if column.key not in before]

    def add_and_fill(
        self, *, list_id: str = "", config: AgentConfig | None = None, agent_id: str = "", max_row_count: int = 0
    ) -> Job:
        """The drawer's two requests on this sheet by default, walked."""
        if config is None and not agent_id:
            config = quick_config()
        _, fill = start_fill(
            list_id or str(self.sheet.id),
            account_id=ACCOUNT,
            user_id=USER,
            config=config,
            agent_id=agent_id,
            max_row_count=max_row_count,
        )
        tick_jobs()
        fill.refresh_from_db()
        return fill


class QuickPathTests(AdmissionTestCase):
    def test_the_drawer_creates_an_ephemeral_column_then_fills_it(self) -> None:
        fill = self.add_and_fill()
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
        self.add_and_fill()
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
        fill = self.add_and_fill(config=config)
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
            self.add_and_fill()
        self.assertIn("already has a answer column", str(caught.exception))

    def test_a_column_an_agent_fills_names_the_better_next_step(self) -> None:
        # Deleting is the wrong advice for a column an agent already
        # fills: re-running it from the column itself is right there,
        # and the answers under it are the user's.
        fill = self.add_and_fill()
        FillService(account_id=ACCOUNT).cancel(str(fill.id))
        self.sheet.refresh_from_db()
        with self.assertRaises(ColumnCollision) as caught:
            self.add_and_fill()
        self.assertIn("Fill all remaining", str(caught.exception))

    def test_occupied_collision_refuses(self) -> None:
        self.sheet.columns = [
            *self.sheet.columns,
            {"kind": "plain", "key": "answer", "label": "Answer", "type": "text"},
        ]
        self.sheet.save(update_fields=["columns"])
        rows = self.lists.rows_page(self.sheet, limit=1)
        type_cells(self.sheet, str(rows[0].id), {"answer": "taken"})
        with self.assertRaises(ColumnCollision):
            self.add_and_fill()
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
            self.add_and_fill(config=config)
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
        self.add_and_fill()
        # A job of another kind on the same list goes with it too.
        JobService(account_id=ACCOUNT).enqueue_system(
            RerankJob(list_id=str(self.sheet.id)), target_id=str(self.sheet.id)
        )
        self.lists.delete(self.sheet)
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        self.assertEqual(Job.objects.filter(target_id=str(self.sheet.id)).count(), 0)
        self.assertEqual(NodeRun.objects.count(), 0)

    def test_an_empty_sheet_takes_the_column_and_refuses_the_fill(self) -> None:
        # Creating a column needs no rows; a fill does. The column stays.
        empty = self.lists.create(owner_id=USER, label="Empty", columns=[], origin="manual")
        keys = self.add_column(list_id=str(empty.id))
        with self.assertRaises(EmptyFill):
            self.admission.fill_column(list_id=str(empty.id), column_key=keys[0])
        empty.refresh_from_db()
        self.assertEqual([column.key for column in empty.columns], ["answer"])

    def test_model_check_refuses_unrunnable(self) -> None:
        # Refused at the create: a config that cannot run never becomes
        # a column, nor an agent.
        self.model_for.side_effect = ModelUnavailable("no such model on this deploy")
        with self.assertRaises(ModelUnrunnable):
            self.add_column()
        self.sheet.refresh_from_db()
        self.assertEqual([column.key for column in self.sheet.columns], ["company"])
        self.assertEqual(Agent.objects.count(), 0)


class GuardTests(AdmissionTestCase):
    def test_same_column_live_run_refuses(self) -> None:
        # One live fill per column: a second fill waits for the first.
        self.add_and_fill()
        with self.assertRaises(SameColumnFillActive):
            self.admission.fill_column(list_id=str(self.sheet.id), column_key="answer")

    def test_a_column_behind_a_barrier_refuses_and_queues_nothing(self) -> None:
        # A fill starts where an arrival does, right behind an entry
        # marker; a node behind a wait is the workflow's to reach.
        # FAILS if the fill starts at any column a node fills.
        self.add_column()
        self.sheet.refresh_from_db()
        first = next(column.node_id for column in self.sheet.columns if column.key == "answer")
        chain_behind(self.sheet, upstream_node_id=first, key="later")
        with self.assertRaises(FillColumnDownstream):
            self.admission.fill_column(list_id=str(self.sheet.id), column_key="later")
        self.assertEqual(fill_progress.fill_jobs().count(), 0)

    def test_a_column_deeper_on_an_entry_path_refuses(self) -> None:
        # The entry action is the FIRST action behind an entry marker; an
        # action further down the same path is the workflow's to reach.
        # FAILS if the check asks only whether the path is entry-headed.
        first = self.admission.agents.create(owner_id=USER, label="First", config=quick_config())
        deep_config = quick_config(outputs=[AgentOutput(key="deep", label="Deep", type="text")])
        deep_agent = self.admission.agents.create(owner_id=USER, label="Deep", config=deep_config)
        _, nodes = WorkflowService(account_id=ACCOUNT).create_path(
            self.sheet,
            [Entry(), ColumnAgent(agent_id=str(first.id)), ColumnAgent(agent_id=str(deep_agent.id))],
        )
        deep = AiColumn(key="deep", label="Deep", type="text", node_id=str(nodes[2].id))
        self.sheet.columns = [*self.sheet.columns, deep]
        self.sheet.save(update_fields=["columns", "updated_at"])
        with self.assertRaises(FillColumnDownstream):
            self.admission.fill_column(list_id=str(self.sheet.id), column_key="deep")
        self.assertEqual(fill_progress.fill_jobs().count(), 0)

    def test_the_fill_checks_the_model_too(self) -> None:
        # A roster agent's model can change after its column exists, so
        # the fill probes the config again and queues nothing when it
        # cannot run. FAILS if the fill skips the check.
        self.add_column()
        self.model_for.side_effect = ModelUnavailable("no such model on this deploy")
        with self.assertRaises(ModelUnrunnable):
            self.admission.fill_column(list_id=str(self.sheet.id), column_key="answer")
        self.assertEqual(fill_progress.fill_jobs().count(), 0)

    def test_the_cap_counts_fills_only(self) -> None:
        # The fills share one table with the other kinds: open system
        # jobs on the account (a re-space, a backfill) are not fills and
        # take no slot. FAILS if the count loses its kind filter.
        for _ in range(MAX_ACTIVE_FILLS):
            JobService(account_id=ACCOUNT).enqueue_system(
                RerankJob(list_id=str(self.sheet.id)), target_id=str(self.sheet.id)
            )
        self.add_and_fill()

    def test_account_cap_refuses(self) -> None:
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.add_and_fill(list_id=str(sheet.id))
        with self.assertRaises(AccountFillsFull):
            self.add_and_fill()

    def test_a_finished_fill_frees_its_account_slot(self) -> None:
        # The cap counts LIVE fills only (the locked count path).
        fills = []
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            fills.append(self.add_and_fill(list_id=str(sheet.id)))
        Job.objects.filter(id=fills[0].id).update(status=JobStatus.DONE)
        fill = self.add_and_fill()
        self.assertEqual(fill_status(str(fill.id)), "pending")

    @override_settings(TOOL_WIRING={"web_search": "duckduckgo"})
    def test_free_search_budget_refuses_wide_tool_fills(self) -> None:
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget) as caught:
            self.add_and_fill(list_id=str(wide.id), config=config)
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
        fill = self.add_and_fill(list_id=str(wide.id), config=config)
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
        fill = self.add_and_fill(list_id=str(wide.id), config=config)
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
            self.add_and_fill(list_id=str(wide.id), config=config)


class ScopedFillTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Positions 3-5: a variable-blank row INSIDE the scope range,
        # then two more eligible rows (1-2 seeded by the base class).
        self.lists.add_rows(self.sheet, [{"company": ""}, {"company": "initech.com"}, {"company": "umbrella.io"}])

    def test_a_scoped_fill_targets_the_first_n_eligible(self) -> None:
        # max_row_count=3 walks rows 1, 2, then SKIPS the variable-blank
        # row at 3 (it would render an empty ask) and takes row 4:
        # first N means first N usable.
        fill = self.add_and_fill(max_row_count=3)
        self.assertEqual(target_row_count(str(fill.id)), 3)
        # The cutoff is the LAST TARGETED row, not the sheet size.
        self.assertEqual(targeted_numbers(str(fill.id)), [1, 2, 4])

    def test_rows_past_the_cutoff_have_no_outcome_rows(self) -> None:
        # Not-attempted is the ABSENCE of an outcome row: row 5
        # (past the scoped cutoff) and row 3 (ineligible) never
        # materialize.
        fill = self.add_and_fill(max_row_count=3)
        self.assertEqual(len(targeted_numbers(str(fill.id))), 3)
        self.assertNotIn(3, targeted_numbers(str(fill.id)))
        self.assertNotIn(5, targeted_numbers(str(fill.id)))

    def test_a_scoped_fill_ranges_over_the_sheet_and_stops_at_n(self) -> None:
        # A scoped fill asked for the first N usable rows; the sheet
        # total was never the number it asked for, so the range is
        # the sheet as it stands and N is the ceiling.
        fill = self.add_and_fill(max_row_count=1)
        self.assertEqual(target_row_count(str(fill.id)), 1)

    def test_no_eligible_rows_refuses(self) -> None:
        # The column is created (its prompt stays fixable from the
        # tracker); the fill is refused and nothing is queued.
        bare = self.lists.create(
            owner_id=USER,
            label="Bare",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(bare, [{"company": ""}, {"other": "unrelated"}])
        with self.assertRaises(NoEligibleRows) as caught:
            self.add_and_fill(list_id=str(bare.id))
        self.assertEqual(str(caught.exception), "No rows have values for this prompt's variables.")
        self.assertEqual(fill_progress.fill_jobs().count(), 0)
        bare.refresh_from_db()
        self.assertEqual([c.key for c in bare.columns], ["company", "answer"])

    def test_variable_less_prompt_treats_every_row_as_eligible(self) -> None:
        # No {{tokens}} means the prompt asks the same question
        # everywhere; the variable-blank row is a target like any other.
        fill = self.add_and_fill(config=quick_config(prompt="Name three colors."))
        self.assertEqual(target_row_count(str(fill.id)), 5)
        self.assertEqual(len(targeted(str(fill.id))), 5)

    @override_settings(TOOL_WIRING={"web_search": "duckduckgo"})
    def test_scope_bounds_the_free_search_budget(self) -> None:
        # The budget reads the TARGET count: a scoped fill on a sheet
        # too wide to run free still starts when N fits the budget.
        wide = self.lists.create(owner_id=USER, label="Wide", columns=[], origin="manual")
        over = FREE_SEARCH_FILL_BUDGET // 4 + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(over)])
        (key,) = self.add_column(list_id=str(wide.id), config=quick_config(tools=AgentTools(web_search=True)))
        with self.assertRaises(FreeSearchBudget):
            self.admission.fill_column(list_id=str(wide.id), column_key=key)
        fill = self.admission.fill_column(list_id=str(wide.id), column_key=key, max_row_count=4)
        self.assertEqual(fill_status(str(fill.id)), "pending")
        tick_jobs()
        self.assertEqual(target_row_count(str(fill.id)), 4)


class RosterPathTests(AdmissionTestCase):
    def test_roster_agent_is_used_not_duplicated(self) -> None:
        agent = self.admission.agents.create(owner_id=USER, label="Finder", config=quick_config())
        fill = self.add_and_fill(config=None, agent_id=str(agent.id))
        self.assertEqual(consent_of(str(fill.id)).agent_id, str(agent.id))
        self.assertEqual(Agent.objects.count(), 1)

    def test_retired_provider_refuses(self) -> None:
        agent = self.admission.agents.create(owner_id=USER, label="Old", config=quick_config())
        Agent.objects.filter(id=agent.id).update(provider="legacy_provider")
        with self.assertRaises(ProviderRetiredRefusal):
            self.add_and_fill(config=None, agent_id=str(agent.id))


class ConsentUnderLockTests(AdmissionTestCase):
    """The column set a fill owns is frozen under the List lock, from
    the locked copy, never from the unlocked read; and no columns
    writer adds a column under a key an open fill still writes."""

    TWO = None

    def setUp(self) -> None:
        super().setUp()
        self.TWO = quick_config(
            outputs=[
                AgentOutput(key="alpha", label="Alpha", type="text"),
                AgentOutput(key="beta", label="Beta", type="text"),
            ]
        )
        self.columns = ColumnService(account_id=ACCOUNT, user_id=USER)

    def test_a_sibling_deleted_before_the_lock_is_not_in_the_consent(self) -> None:
        # The delete lands between the fill's unlocked read and its lock:
        # its cancel loop cannot see a fill not yet opened, so the fill
        # must not open owning the deleted key. FAILS if the consent is
        # frozen off the unlocked read.
        self.add_column(config=self.TWO)
        real = self.admission._list_or_raise
        deleted = []

        def delete_then_lock(list_id: str, *, lock: bool = False):
            if lock and not deleted:
                deleted.append(True)
                self.columns.delete(list_id, key="beta")
            return real(list_id, lock=lock)

        with patch.object(self.admission, "_list_or_raise", side_effect=delete_then_lock):
            fill = self.admission.fill_column(list_id=str(self.sheet.id), column_key="alpha")
        self.assertEqual(consent_of(str(fill.id)).column_keys, ["alpha"])
        self.sheet.refresh_from_db()
        pointers = {column.key: column.current_fill_id for column in self.sheet.columns if column.kind == "ai"}
        self.assertEqual(pointers, {"alpha": str(fill.id)})

    def test_a_plain_or_ai_column_is_refused_under_a_live_fills_key(self) -> None:
        # A live fill on alpha and beta; beta is deleted with the fill
        # left open by construction (its consent still names beta), and
        # both writers refuse beta. FAILS if either writer ignores open
        # consents.
        fill = self.add_and_fill(config=self.TWO)
        self.assertEqual(fill_status(str(fill.id)), "pending")
        # Take beta off the sheet WITHOUT the delete's cancel, the state
        # the locked consent makes unreachable except by a fill already
        # claimed: the columns array is edited directly.
        self.sheet.refresh_from_db()
        self.sheet.columns = [column for column in self.sheet.columns if column.key != "beta"]
        self.sheet.save(update_fields=["columns", "updated_at"])
        with self.assertRaises(ColumnExists):
            self.columns.add_column(str(self.sheet.id), label="Beta", column_type="text")
        with self.assertRaises(ColumnCollision):
            self.add_column(config=quick_config(outputs=[AgentOutput(key="beta", label="Beta", type="text")]))


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
        # The walk is the job's: the fill request queues it and writes
        # the column under the lock, nothing else. A 50,000 row sheet
        # costs the request one probe page.
        (key,) = self.add_column()
        with CaptureQueriesContext(connection) as captured:
            self.admission.fill_column(list_id=str(self.sheet.id), column_key=key)
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
            self.add_and_fill(list_id=str(sheet.id))
        queued_before = Job.objects.count()
        with CaptureQueriesContext(connection) as captured, self.assertRaises(AccountFillsFull):
            self.add_and_fill()
        inserts = [s for s in self.sql(captured) if s.startswith('INSERT INTO "JOBS_JOB"')]
        self.assertEqual(inserts, [], "the cap was knowable before the walk was queued")
        self.assertEqual(Job.objects.count(), queued_before)

    def test_at_both_caps_the_column_add_refuses_first(self) -> None:
        # The column is created before a fill is asked for, so a sheet at
        # its column cap refuses the add whatever the fill cap says:
        # without the column there is nothing to fill.
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(owner_id=USER, label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.add_and_fill(list_id=str(sheet.id))
        wide = self.lists.create(
            owner_id=USER,
            label="Wide",
            columns=[
                {"kind": "plain", "key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)
            ],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        with self.assertRaises(ColumnsFull):
            self.add_column(list_id=str(wide.id))

    def test_a_full_sheet_refuses_before_anything_is_written(self) -> None:
        wide = self.lists.create(
            owner_id=USER,
            label="Wide",
            columns=[
                {"kind": "plain", "key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)
            ],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        # The cap is judged under the lock before the agent or node is
        # written, so the refusal inserts no row at all. FAILS if the
        # cap check moves after any write.
        with CaptureQueriesContext(connection) as captured, self.assertRaises(ColumnsFull):
            self.add_column(list_id=str(wide.id))
        inserts = [s for s in self.sql(captured) if s.startswith("INSERT")]
        self.assertEqual(inserts, [], "the column cap was knowable before anything was written")

    def test_the_columns_array_is_written_ONCE(self) -> None:
        # Once by the create (the columns appended) and once by the fill
        # (the columns pointed at it): each request's one write, so the
        # lock never has to span the queue insert.
        for step in ("create", "fill"):
            with self.subTest(step=step), CaptureQueriesContext(connection) as captured:
                if step == "create":
                    (key,) = self.add_column()
                else:
                    self.admission.fill_column(list_id=str(self.sheet.id), column_key=key)
            writes = [s for s in self.sql(captured) if s.startswith('UPDATE "LISTS_LIST"')]
            self.assertEqual(len(writes), 1, writes)

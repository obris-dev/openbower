"""Admission: the one transaction. Real DB and services; model_for is
the one patched seam (the framework's wire is not ours to test), per
the runtime tests' precedent."""

from __future__ import annotations

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings

from agents.models import Agent, AgentTestRun
from agents.providers import ModelUnavailable
from agents.services import config_fingerprint
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import CellRunResult

from ..constants import (
    FREE_SEARCH_FILL_BUDGET,
    MAX_ACTIVE_FILLS,
    MAX_LIST_COLUMNS,
    FillStatus,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask
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
    RowCountChanged,
    SameColumnFillActive,
)
from ..services.fills import FillService
from ..services.lists import ListService
from .fill_helpers import queued_row_ids, targeted, targeted_positions

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


class AdmissionTestCase(TestCase):
    def setUp(self) -> None:
        self.lists = ListService(account_id=ACCOUNT, user_id=USER)
        self.admission = FillAdmissionService(account_id=ACCOUNT, user_id=USER)
        self.fills = FillService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            label="Prospects", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        patcher = patch("lists.services.fill_admission.model_for")
        self.model_for = patcher.start()
        self.addCleanup(patcher.stop)

    def admit(self, **overrides):
        kwargs = {
            "list_id": str(self.sheet.id),
            "config": quick_config(),
            "confirmed_row_count": 2,
        }
        kwargs.update(overrides)
        return self.admission.admit(**kwargs)


class QuickPathTests(AdmissionTestCase):
    def test_admit_creates_ephemeral_column_run_and_queue(self) -> None:
        fill = self.admit()
        agent = Agent.objects.get(id=fill.agent_id)
        self.assertTrue(agent.ephemeral)
        # The ephemeral row's label is the FIRST output's.
        self.assertEqual(agent.label, "Answer")
        self.sheet.refresh_from_db()
        added = [c for c in self.sheet.columns if c["key"] == "answer"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["label"], "Answer")
        # The column carries BOTH custody facts: which agent fills it
        # and which fill currently speaks for it (stored, not walked).
        self.assertEqual(added[0]["fill"], {"agent_id": str(agent.id), "current_fill_id": str(fill.id)})
        self.assertEqual(fill.status, FillStatus.PENDING)
        self.assertEqual(fill.column_keys, ["answer"])
        self.assertEqual(fill.config_snapshot["model"], "test-model")
        self.assertEqual(len(targeted(str(fill.id))), 2)

    def test_multi_output_columns_are_the_outputs_own_keys(self) -> None:
        config = quick_config(
            outputs=[
                AgentOutput(key="email", label="Email", type="email"),
                AgentOutput(key="status", label="Status", type="text"),
            ]
        )
        fill = self.admit(config=config)
        self.assertEqual(fill.column_keys, ["email", "status"])
        self.sheet.refresh_from_db()
        keys = {c["key"] for c in self.sheet.columns}
        self.assertIn("email", keys)
        self.assertIn("status", keys)
        labels = {c["key"]: c["label"] for c in self.sheet.columns}
        self.assertEqual(labels["email"], "Email")
        self.assertEqual(labels["status"], "Status")

    def test_an_existing_column_refuses_whatever_is_in_it(self) -> None:
        # EXISTENCE is the rule, not occupancy. Asking whether a column
        # held any value meant scanning the sheet with no index behind
        # it, under the List lock, to decide something the user can see
        # for themselves: the column is there.
        self.sheet.columns = [*self.sheet.columns, {"key": "answer", "label": "Answer", "type": "text"}]
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
        self.sheet.columns = [*self.sheet.columns, {"key": "answer", "label": "Answer", "type": "text"}]
        self.sheet.save(update_fields=["columns"])
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=1)
        self.lists.write_cells(str(self.sheet.id), str(rows[0].id), {"answer": "taken"})
        with self.assertRaises(ColumnCollision):
            self.admit()
        # Nothing committed: no fill, no ephemeral, no columns change.
        self.assertEqual(Fill.objects.count(), 0)
        self.assertEqual(Agent.objects.count(), 0)

    def test_duplicate_output_keys_refuse(self) -> None:
        # The request serializer refuses duplicate keys at the door,
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
        self.assertEqual(Fill.objects.count(), 0)
        self.sheet.refresh_from_db()
        self.assertEqual([c["key"] for c in self.sheet.columns], ["company"])

    def test_list_delete_purges_runs_and_outcomes(self) -> None:
        # No cascades exist: delete() owns the fill custody's cleanup,
        # or a live orphaned fill holds an account fill slot forever
        # with nothing visible to cancel.
        self.admit()
        self.lists.delete(self.sheet)
        self.assertEqual(Fill.objects.count(), 0)
        self.assertEqual(FillTask.objects.count(), 0)

    def test_row_count_echo_refuses_on_growth(self) -> None:
        with self.assertRaises(RowCountChanged) as caught:
            self.admit(confirmed_row_count=1)
        self.assertEqual(caught.exception.actual, 2)

    def test_a_shrunken_sheet_admits_and_fills_less(self) -> None:
        # Reviewed 99, the sheet has 2: fewer rows than consented is
        # cheaper, never a betrayal, so the echo is growth-only.
        fill = self.admit(confirmed_row_count=99)
        self.assertEqual(fill.confirmed_row_count, 2)

    def test_empty_sheet_refuses(self) -> None:
        empty = self.lists.create(label="Empty", columns=[], origin="manual")
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

    def test_account_cap_refuses(self) -> None:
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        with self.assertRaises(AccountFillsFull):
            self.admit()

    def test_a_finished_fill_frees_its_account_slot(self) -> None:
        # The cap counts LIVE fills only (the locked count path).
        fills = []
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            fills.append(self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1))
        Fill.objects.filter(id=fills[0].id).update(status=FillStatus.COMPLETE)
        fill = self.admit()
        self.assertEqual(fill.status, FillStatus.PENDING)

    def test_free_search_budget_refuses_wide_tool_fills(self) -> None:
        wide = self.lists.create(label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget) as caught:
            self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)
        # Rendered verbatim in the drawer: user words only (no internal
        # door vocabulary), and the next step is the paid provider.
        self.assertEqual(
            str(caught.exception),
            f"This fill could need up to {rows * MAX_TOOL_CALLS:,} searches; free search is budgeted for "
            f"{FREE_SEARCH_FILL_BUDGET} per fill. Connect DataForSEO for metered search.",
        )

    @override_settings(DATAFORSEO_LOGIN="login", DATAFORSEO_PASSWORD="secret")
    def test_paid_door_lifts_the_free_budget(self) -> None:
        wide = self.lists.create(label="Wide", columns=[], origin="manual")
        rows = FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(rows)])
        config = quick_config(tools=AgentTools(web_search=True))
        fill = self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=rows)
        self.assertEqual(fill.status, FillStatus.PENDING)


class ScopedFillTests(AdmissionTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Positions 3-5: a variable-blank row INSIDE the scope range,
        # then two more eligible rows (1-2 seeded by the base class).
        self.lists.add_rows(self.sheet, [{"company": ""}, {"company": "initech.com"}, {"company": "umbrella.io"}])

    def test_scoped_admit_targets_the_first_n_eligible(self) -> None:
        # rows=3 walks positions 1, 2, then SKIPS the variable-blank
        # row at 3 (it would render an empty ask) and takes position 4:
        # first N means first N usable.
        fill = self.admit(rows=3, confirmed_row_count=5)
        self.assertEqual(fill.confirmed_row_count, 3)
        # The cutoff is the LAST TARGETED position, not the sheet size.
        self.assertEqual(targeted_positions(str(fill.id)), [1, 2, 4])

    def test_rows_past_the_cutoff_have_no_outcome_rows(self) -> None:
        # Not-attempted is the ABSENCE of an outcome row: position 5
        # (past the scoped cutoff) and position 3 (ineligible) never
        # materialize.
        fill = self.admit(rows=3, confirmed_row_count=5)
        self.assertEqual(len(targeted_positions(str(fill.id))), 3)
        self.assertNotIn(3, targeted_positions(str(fill.id)))
        self.assertNotIn(5, targeted_positions(str(fill.id)))

    def test_scoped_admit_skips_the_growth_echo_unscoped_keeps_it(self) -> None:
        # A scoped fill asked for the first N usable rows; the sheet
        # total was never the number it consented to, so drift in it is
        # irrelevant. Unscoped, the echo still refuses.
        with self.assertRaises(RowCountChanged):
            self.admit(confirmed_row_count=1)
        fill = self.admit(rows=1, confirmed_row_count=1)
        self.assertEqual(fill.confirmed_row_count, 1)

    def test_no_eligible_rows_refuses(self) -> None:
        bare = self.lists.create(
            label="Bare", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(bare, [{"company": ""}, {"other": "unrelated"}])
        with self.assertRaises(NoEligibleRows) as caught:
            self.admission.admit(list_id=str(bare.id), config=quick_config(), confirmed_row_count=2)
        self.assertEqual(str(caught.exception), "No rows have values for this prompt's variables.")
        # Nothing committed: no fill, no ephemeral, no columns change.
        self.assertEqual(Fill.objects.count(), 0)
        self.assertEqual(Agent.objects.count(), 0)
        bare.refresh_from_db()
        self.assertEqual([c["key"] for c in bare.columns], ["company"])

    def test_variable_less_prompt_treats_every_row_as_eligible(self) -> None:
        # No {{tokens}} means the prompt asks the same question
        # everywhere; the variable-blank row is a target like any other.
        fill = self.admit(config=quick_config(prompt="Name three colors."), confirmed_row_count=5)
        self.assertEqual(fill.confirmed_row_count, 5)
        self.assertEqual(len(targeted(str(fill.id))), 5)

    def test_scope_bounds_the_free_search_budget(self) -> None:
        # The budget reads the TARGET count: a scoped fill on a sheet
        # too wide to run free still admits when N fits the budget.
        wide = self.lists.create(label="Wide", columns=[], origin="manual")
        over = FREE_SEARCH_FILL_BUDGET // 4 + 1
        self.lists.add_rows(wide, [{"company": f"a{n}.com"} for n in range(over)])
        config = quick_config(tools=AgentTools(web_search=True))
        with self.assertRaises(FreeSearchBudget):
            self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=over)
        fill = self.admission.admit(list_id=str(wide.id), config=config, confirmed_row_count=over, rows=4)
        self.assertEqual(fill.status, FillStatus.PENDING)
        self.assertEqual(fill.confirmed_row_count, 4)


class RosterPathTests(AdmissionTestCase):
    def test_roster_agent_is_used_not_duplicated(self) -> None:
        agent = self.admission.agents.create(label="Finder", config=quick_config())
        fill = self.admit(config=None, agent_id=str(agent.id))
        self.assertEqual(fill.agent_id, str(agent.id))
        self.assertEqual(Agent.objects.count(), 1)

    def test_retired_provider_refuses(self) -> None:
        agent = self.admission.agents.create(label="Old", config=quick_config())
        Agent.objects.filter(id=agent.id).update(provider="legacy_door")
        with self.assertRaises(ProviderRetiredRefusal):
            self.admit(config=None, agent_id=str(agent.id))


class BenchSeedTests(AdmissionTestCase):
    def _run_for(self, config: AgentConfig, *, row_id: str, cells: dict[str, str]) -> AgentTestRun:
        return AgentTestRun.objects.create(
            account_id=ACCOUNT,
            user_id=USER,
            status="complete",
            config_fingerprint=config_fingerprint(config),
            row_id=row_id,
            result={"cells": cells, "evidence": ["seen"], "searches": []},
        )

    def test_config_identical_run_seeds_the_borrowed_row(self) -> None:
        config = quick_config()
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "seeded"})
        rows_other = self.lists.rows_page(self.sheet, after_position=0, limit=2)[1]
        fill = self.admit(config=config, test_run_id=str(run.id))
        row.refresh_from_db()
        self.assertEqual(row.data["answer"], "seeded")
        seeded = FillTask.objects.get(fill_id=str(fill.id), row_id=str(row.id))
        self.assertEqual(seeded.status, FillTaskStatus.DONE)
        # Both rows were CONSENTED, so both have a task; only the
        # unseeded one is queued for a worker. The seed's task carries
        # the bench run's own result, so the row is never re-billed and
        # the drawer reads it where it reads every other run.
        self.assertEqual(len(targeted(str(fill.id))), 2)
        self.assertEqual(queued_row_ids(str(fill.id)), [str(rows_other.id)])

    def test_a_run_stored_under_retired_causes_is_a_miss_not_a_500(self) -> None:
        # A bench run written before the cell-state vocabulary changed
        # can carry a retired cause word, and borrowing it would hand
        # that word to the landing's strict StoredCellState(). The
        # economy's own rule covers it (one more miss, the row runs
        # fresh); FAILS with a ValueError inside admit without the
        # vocabulary check in _borrowed_row.
        config = quick_config()
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={})
        run.result = {**run.result, "blank_cause": "search_throttled", "declined_cause": "search_throttled"}
        run.save(update_fields=["result"])
        fill = self.admit(config=config, test_run_id=str(run.id))
        row.refresh_from_db()
        self.assertNotIn("answer", row.data)
        task = FillTask.objects.get(fill_id=str(fill.id), row_id=str(row.id))
        self.assertEqual(task.status, FillTaskStatus.QUEUED)

    def test_a_seeded_cell_passes_the_new_columns_type_validator(self) -> None:
        # The seed used to write before the column was claimed, so
        # write_cells found no type for the key and stored the value
        # raw: a number cell kept "1,234" un-normalized and a non-number
        # was recorded FILLED instead of TYPE_MISMATCH.
        config = quick_config(outputs=[AgentOutput(key="answer", label="Answer", type="number")])
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "1,234"})
        self.admit(config=config, test_run_id=str(run.id))
        row.refresh_from_db()
        self.assertEqual(row.data["answer"], "1234")

    def test_a_seeded_non_number_is_a_type_mismatch_not_a_fill(self) -> None:
        config = quick_config(outputs=[AgentOutput(key="answer", label="Answer", type="number")])
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "about $5M"})
        fill = self.admit(config=config, test_run_id=str(run.id))
        row.refresh_from_db()
        self.assertNotIn("answer", row.data)
        state = FillCellState.objects.get(fill_id=str(fill.id), row_id=str(row.id), column_key="answer")
        self.assertEqual(state.state, StoredCellState.TYPE_MISMATCH)

    def test_a_fully_seeded_fill_completes_at_admission(self) -> None:
        # A one-row sheet whose only row seeds from the bench leaves
        # ZERO claimable rows: no worker would ever visit the fill, so
        # admission itself must complete it (or the chip spins forever
        # and the column locks behind a fill that already succeeded).
        config = quick_config()
        solo = self.lists.create(
            label="Solo", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(solo, [{"company": "acme.com"}])
        row = self.lists.rows_page(solo, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "seeded"})
        fill = self.admission.admit(
            list_id=str(solo.id),
            config=config,
            confirmed_row_count=1,
            test_run_id=str(run.id),
        )
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        # The seed leaves ONE task, already DONE: the row is recorded as
        # consented and answered, and nothing is queued for a worker.
        self.assertEqual(queued_row_ids(str(fill.id)), [])
        row.refresh_from_db()
        self.assertEqual(row.data["answer"], "seeded")

    def test_a_seeded_row_is_a_resolved_row_everywhere(self) -> None:
        # A seeded row is RESOLVED, not skipped, so it has to look
        # resolved from every angle the rest of the feature reads. The
        # value alone is not enough: absence of a cell state means NEVER
        # ATTEMPTED, and the counters are what the tracker renders.
        config = quick_config()
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "seeded"})
        fill = self.admit(config=config, test_run_id=str(run.id))
        state = FillCellState.objects.get(list_id=str(self.sheet.id), row_id=str(row.id), column_key="answer")
        self.assertEqual(state.state, StoredCellState.FILLED)
        self.assertEqual(state.fill_id, str(fill.id))
        self.assertEqual((fill.attempted, fill.filled, fill.blank), (1, 1, 0))

    def test_a_seeded_task_stores_the_same_shape_a_run_task_does(self) -> None:
        # The seed and the worker both write FillTask.result, so a
        # reader must not have to ask which one produced a record. Both
        # go through CellRunResult; this pins the seed's half.
        config = quick_config()
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "seeded"})
        fill = self.admit(config=config, test_run_id=str(run.id))
        task = FillTask.objects.get(fill_id=str(fill.id), row_id=str(row.id))
        stored = CellRunResult.model_validate(task.result)
        self.assertEqual(stored.cells, {"answer": "seeded"})
        self.assertEqual(set(task.result), set(CellRunResult.model_fields))

    def test_a_fully_seeded_fill_reports_its_row_as_run(self) -> None:
        # The tracker reads attempted off the column summary. A COMPLETE
        # fill whose only row is answered must not read "1 row to fill",
        # which is both false and unreachable: the row holds a value, so
        # no refill can target it.
        config = quick_config()
        solo = self.lists.create(
            label="Solo", columns=[{"key": "company", "label": "Company", "type": "text"}], origin="manual"
        )
        self.lists.add_rows(solo, [{"company": "acme.com"}])
        row = self.lists.rows_page(solo, after_position=0, limit=1)[0]
        run = self._run_for(config, row_id=str(row.id), cells={"answer": "seeded"})
        fill = self.admission.admit(list_id=str(solo.id), config=config, confirmed_row_count=1, test_run_id=str(run.id))
        self.assertEqual(fill.status, FillStatus.COMPLETE)
        solo.refresh_from_db()
        summary = next(s for s in self.fills.column_summaries(solo) if s.column_key == "answer")
        self.assertEqual((summary.attempted, summary.filled), (1, 1))

    def test_config_drift_skips_the_seed(self) -> None:
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = self._run_for(quick_config(prompt="Different {{company}}"), row_id=str(row.id), cells={"answer": "x"})
        fill = self.admit(test_run_id=str(run.id))
        row.refresh_from_db()
        self.assertNotIn("answer", row.data)
        # The row runs normally: economy, never a gate.
        self.assertEqual(len(targeted(str(fill.id))), 2)

    def test_handfed_run_without_row_skips_the_seed(self) -> None:
        config = quick_config()
        run = self._run_for(config, row_id="", cells={"answer": "x"})
        fill = self.admit(config=config, test_run_id=str(run.id))
        self.assertEqual(len(targeted(str(fill.id))), 2)


class AdmissionLockSpanTests(AdmissionTestCase):
    """Where admission takes the List row lock, and for how long.

    The lock exists for ONE thing, the columns array write, and the
    expensive part of admission (a FillTask per targeted row) must not
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

    def test_the_lock_is_taken_AFTER_the_queue_is_inserted(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            self.admit()
        sql = self.sql(captured)
        locked_at = self.index_of(sql, lambda s: '"LISTS_LIST"' in s and "FOR UPDATE" in s)
        queued_at = self.index_of(sql, lambda s: s.startswith('INSERT INTO "LISTS_FILLTASK"'))
        self.assertLess(
            queued_at,
            locked_at,
            "the queue insert must run BEFORE the List lock is taken, or a large fill blocks "
            "every other sheet-level write for the length of its insert",
        )

    def test_a_seeded_admit_takes_the_list_lock_before_the_row_lock(self) -> None:
        # The seed writes a ListRow through write_cells, which locks
        # that row. Every delete path takes List first, then ListRows,
        # and its comments call the reverse order an ABBA deadlock. So
        # the seed must settle AFTER the List lock, never before.
        config = quick_config()
        row = self.lists.rows_page(self.sheet, after_position=0, limit=1)[0]
        run = AgentTestRun.objects.create(
            account_id=ACCOUNT,
            user_id=USER,
            status="complete",
            config_fingerprint=config_fingerprint(config),
            row_id=str(row.id),
            result={"cells": {"answer": "seeded"}, "evidence": ["seen"], "searches": []},
        )
        with CaptureQueriesContext(connection) as captured:
            self.admit(config=config, test_run_id=str(run.id))
        sql = self.sql(captured)
        # Quoted, because a bare LISTS_LIST also matches LISTS_LISTROW
        # and would bind both indexes to the same statement.
        list_lock = self.index_of(sql, lambda s: '"LISTS_LIST"' in s and "FOR UPDATE" in s)
        row_lock = self.index_of(sql, lambda s: '"LISTS_LISTROW"' in s and "FOR UPDATE" in s)
        self.assertLess(list_lock, row_lock, "the seed's row lock must come AFTER the List lock")

    def test_deterministic_refusals_never_build_the_queue(self) -> None:
        # An account at its cap and a sheet at its column cap are both
        # knowable before any work: hearing the "no" after inserting up
        # to 50,000 FillTask rows would waste the build EVERY time, not
        # on a race.
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        with CaptureQueriesContext(connection) as captured, self.assertRaises(AccountFillsFull):
            self.admit()
        inserts = [s for s in self.sql(captured) if s.startswith('INSERT INTO "LISTS_FILLTASK"')]
        self.assertEqual(inserts, [], "the cap was knowable before the queue was built")

    def test_at_both_caps_the_fill_cap_wins(self) -> None:
        # fills_full is a 409 whose fix is waiting; columns_full is a
        # 400 whose fix is changing the sheet. An account at both must
        # hear the one that waiting actually fixes, the same precedence
        # the locked claim keeps.
        for n in range(MAX_ACTIVE_FILLS):
            sheet = self.lists.create(label=f"S{n}", columns=[], origin="manual")
            self.lists.add_rows(sheet, [{"company": "acme.com"}])
            self.admission.admit(list_id=str(sheet.id), config=quick_config(), confirmed_row_count=1)
        wide = self.lists.create(
            label="Wide",
            columns=[{"key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        with self.assertRaises(AccountFillsFull):
            self.admission.admit(list_id=str(wide.id), config=quick_config(), confirmed_row_count=1)

    def test_a_full_sheet_refuses_before_building_the_queue(self) -> None:
        wide = self.lists.create(
            label="Wide",
            columns=[{"key": f"c{n}", "label": f"C{n}", "type": "text"} for n in range(MAX_LIST_COLUMNS)],
            origin="manual",
        )
        self.lists.add_rows(wide, [{"c0": "x"}])
        with CaptureQueriesContext(connection) as captured, self.assertRaises(ColumnsFull):
            self.admission.admit(list_id=str(wide.id), config=quick_config(), confirmed_row_count=1)
        inserts = [s for s in self.sql(captured) if s.startswith('INSERT INTO "LISTS_FILLTASK"')]
        self.assertEqual(inserts, [], "the column cap was knowable before the queue was built")

    def test_the_columns_array_is_written_ONCE(self) -> None:
        # It used to be written twice: the append, then a separate
        # current_fill_id stamp, with the queue insert in between.
        # Those two writes are what forced the lock to span the insert.
        with CaptureQueriesContext(connection) as captured:
            self.admit()
        writes = [s for s in self.sql(captured) if s.startswith('UPDATE "LISTS_LIST"')]
        self.assertEqual(len(writes), 1, writes)

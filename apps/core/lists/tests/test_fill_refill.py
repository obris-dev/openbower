"""Refill: the one recovery primitive, through the endpoint with real
cookie auth (the IdP mocked at its httpx boundary via the shared login
helper). model_for is the one patched seam, per the fill views'
precedent; responses validate back through the contract models.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_fill_refill
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import AgentConfig, AgentOutput, AgentTools
from openbower_schema.fills import FillRunWire

from ..constants import FillStatus, StoredCellState
from ..models import Fill, FillCellState, FillTask
from ..services.fill_admission import FillAdmissionService
from ..services.lists import ListService
from .fill_helpers import FILLED_VALUE, row_value, settle, settle_all, targeted, targeted_pairs, targeted_positions

# Request-shaped config (the serializer derives the output key).
CONFIG = {
    "prompt": "Find the answer for {{company}}",
    "provider": "openai_compatible",
    "source": "ollama",
    "model": "test-model",
    "tools": {},
    "outputs": [{"label": "Answer", "type": "text"}],
}


class RefillTestCase(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.lists = ListService(account_id=TEST_IDENTITY["account_id"])
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[{"key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        patcher = patch("lists.services.fill_admission.base.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)

    def admit(self, confirmed_row_count: int = 2, rows: int = 0) -> dict:
        body: dict = {"config": CONFIG, "confirmed_row_count": confirmed_row_count}
        if rows:
            body["rows"] = rows
        resp = self.client.post(
            reverse("lists_columns_ai", kwargs={"id": str(self.sheet.id)}),
            body,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def cancel(self, fill_run_id: str) -> None:
        resp = self.client.post(
            reverse("lists_fill_cancel", kwargs={"id": str(self.sheet.id), "fill_run_id": fill_run_id})
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def refill(self, list_id: str = "", key: str = "answer", rows: int = 0):
        url = reverse("lists_column_refill", kwargs={"id": list_id or str(self.sheet.id), "key": key})
        if rows:
            return self.client.post(url, {"rows": rows}, content_type="application/json")
        return self.client.post(url)


class RefillTargetTests(RefillTestCase):
    def test_refill_runs_only_unanswered_blank_rows(self) -> None:
        # Four rows; the stopped fill answered one (FILLED outcome) and
        # the user typed into another. Both are EXCLUDED: an answered
        # cell is never re-run and never re-billed, and running a
        # user-entered cell would spend on a write-if-blank skip.
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}, {"company": "umbrella.io"}])
        fill = self.admit(confirmed_row_count=4)
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), None)
        self.lists.write_cells(str(self.sheet.id), str(rows[2].id), {"answer": "typed by hand"})
        self.cancel(fill["id"])

        resp = self.refill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.status, "pending")
        self.assertEqual(wire.column_keys, ["answer"])
        # Consent facts: the TARGET count, not the sheet total; the
        # cutoff is the current row count.
        self.assertEqual(wire.confirmed_row_count, 2)
        self.assertEqual(wire.counters.attempted, 0)
        self.assertEqual(targeted_pairs(wire.id), [(str(rows[1].id), 2), (str(rows[3].id), 4)])

    def test_appended_rows_are_covered(self) -> None:
        # Fill-remaining and resume are ONE primitive: the new fill's
        # cutoff is the current row count, so rows appended after the
        # stopped fill fall inside the target set.
        fill = self.admit()
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}])

        resp = self.refill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.confirmed_row_count, 3)
        self.assertEqual(len(targeted(wire.id)), 3)

    def test_empty_target_refuses_with_the_envelope(self) -> None:
        fill = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle_all(fill["id"], None)
        for row in rows:
            self.lists.write_cells(str(self.sheet.id), str(row.id), {"answer": "done"})
        self.cancel(fill["id"])

        resp = self.refill()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "refill_empty", "detail": "Every row of this column already has an answer."},
        )

    def test_same_column_live_run_is_409(self) -> None:
        self.admit()
        resp = self.refill()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json(), {"error": "fill_active", "detail": "A fill is already running on this column."})

    def test_fresh_snapshot_picks_up_an_agent_edit(self) -> None:
        # Mid-fill edits never apply (the snapshot freezes at
        # admission); a refill is exactly when they SHOULD.
        fill = self.admit()
        self.cancel(fill["id"])
        Agent.objects.filter(id=fill["agent_id"]).update(prompt="Reworded ask for {{company}}")

        resp = self.refill()
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.agent_id, fill["agent_id"])
        # The snapshot is stored, not wired: the refill's freshness is
        # asserted where the worker will read it.
        self.assertEqual(Fill.objects.get(id=wire.id).config_snapshot["prompt"], "Reworded ask for {{company}}")


class SettledBlankTests(RefillTestCase):
    def test_settled_blanks_stay_settled_and_infrastructure_reruns(self) -> None:
        # An honest no-evidence blank re-buys the same nothing under
        # the same config: settled. Infrastructure-tier outcomes
        # (model_error) re-run.
        fill = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), StoredCellState.NO_EVIDENCE)
        settle(fill["id"], str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.cancel(fill["id"])
        refill = self.refill()
        self.assertEqual(refill.status_code, 201, refill.content)
        owed = targeted(refill.json()["id"])
        self.assertNotIn(str(rows[0].id), owed)
        self.assertIn(str(rows[1].id), owed)

    def test_a_no_answer_tombstone_is_not_retargeted(self) -> None:
        # Budget exhaustion (the model spent its budget without
        # answering) is SETTLED: the same config re-buys the same
        # refusal, so refill skips it while model_error still re-runs.
        fill = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), StoredCellState.NO_ANSWER)
        settle(fill["id"], str(rows[1].id), StoredCellState.MODEL_ERROR)
        self.cancel(fill["id"])
        refill = self.refill()
        self.assertEqual(refill.status_code, 201, refill.content)
        owed = targeted(refill.json()["id"])
        self.assertNotIn(str(rows[0].id), owed)
        self.assertIn(str(rows[1].id), owed)

    def _edit_prompt(self, agent_id: str, prompt: str) -> None:
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(agent_id)
        agents.update(agent, config=agent.config().model_copy(update={"prompt": prompt}))

    def test_an_edited_prompt_reopens_blanks_settled_under_the_old_one(self) -> None:
        # Settled means settled UNDER THAT CONFIG: a no_answer verdict
        # was the model's refusal of the OLD ask, so a changed prompt
        # re-targets the row on the next refill.
        fill = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), StoredCellState.NO_ANSWER)
        self.cancel(fill["id"])
        self._edit_prompt(fill["agent_id"], "A sharper ask for {{company}}")
        refill = self.refill()
        self.assertEqual(refill.status_code, 201, refill.content)
        owed = targeted(refill.json()["id"])
        self.assertIn(str(rows[0].id), owed)

    def test_a_filled_row_is_never_retargeted_even_after_an_edit(self) -> None:
        # An answer is an answer: FILLED is settled regardless of what
        # config produced it.
        fill = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), None)
        self.cancel(fill["id"])
        self._edit_prompt(fill["agent_id"], "A sharper ask for {{company}}")
        refill = self.refill()
        self.assertEqual(refill.status_code, 201, refill.content)
        owed = targeted(refill.json()["id"])
        self.assertNotIn(str(rows[0].id), owed)
        self.assertIn(str(rows[1].id), owed)

    def test_the_newest_runs_verdict_outranks_older_ones(self) -> None:
        # Settled in a NEWER fill stays settled even when an older fill
        # errored the same row.
        first = self.admit()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(first["id"], str(rows[0].id), StoredCellState.MODEL_ERROR)
        self.cancel(first["id"])
        second = self.refill()
        self.assertEqual(second.status_code, 201, second.content)
        second_id = second.json()["id"]
        settle(second_id, str(rows[0].id), StoredCellState.NO_EVIDENCE)
        self.cancel(second_id)
        third = self.refill()
        self.assertEqual(third.status_code, 201, third.content)
        owed = targeted(third.json()["id"])
        self.assertNotIn(str(rows[0].id), owed)


class PartialAnswerTests(RefillTestCase):
    def test_an_unanswered_output_stays_targetable(self) -> None:
        # A run answers outputs independently: settling the whole row
        # FILLED because ONE output landed leaves the other column
        # permanently blank AND permanently settled, unreachable by
        # any gesture.
        two = {**CONFIG, "outputs": [{"label": "Alpha", "type": "text"}, {"label": "Beta", "type": "text"}]}
        resp = self.client.post(
            reverse("lists_columns_ai", kwargs={"id": str(self.sheet.id)}),
            {"config": two, "confirmed_row_count": 2},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        fill = resp.json()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        # Alpha answered, Beta declined, exactly as a real run reports.
        settle(fill["id"], str(rows[0].id), causes={"beta": StoredCellState.NO_EVIDENCE})
        settle(fill["id"], str(rows[1].id), None)
        self.cancel(fill["id"])

        # Alpha answered and beta did not, on the SAME row: each column
        # carries its own state, so beta stays targetable instead of
        # settling as answered because a sibling landed.
        states = {(c.row_id, c.column_key): c.state for c in FillCellState.objects.filter(list_id=str(self.sheet.id))}
        self.assertEqual(states[(str(rows[0].id), "alpha")], StoredCellState.FILLED)
        self.assertEqual(states[(str(rows[0].id), "beta")], StoredCellState.NO_EVIDENCE)
        self.assertEqual(row_value(str(self.sheet.id), str(rows[0].id), "alpha"), FILLED_VALUE)
        # And the blank column is reachable again once the ask changes.
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(fill["agent_id"])
        agents.update(agent, config=agent.config().model_copy(update={"prompt": "A sharper ask for {{company}}"}))
        refill = self.refill(key="beta")
        self.assertEqual(refill.status_code, 201, refill.content)
        owed = targeted(refill.json()["id"])
        self.assertIn(str(rows[0].id), owed)


class MultiColumnResumeTests(RefillTestCase):
    """A fill owns one column per output. Continue is FILL-scoped, so
    what it still owes is judged across EVERY column it owns, not the
    one the calling surface happened to name."""

    def _two_output_fill(self) -> dict:
        two = {**CONFIG, "outputs": [{"label": "Alpha", "type": "text"}, {"label": "Beta", "type": "text"}]}
        resp = self.client.post(
            reverse("lists_columns_ai", kwargs={"id": str(self.sheet.id)}),
            {"config": two, "confirmed_row_count": 2},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def _resume(self, fill_run_id: str, key: str):
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": key})
        return self.client.post(url, {"resume_fill": fill_run_id}, content_type="application/json")

    def test_continue_owes_an_abandoned_row_whose_first_column_is_answered(self) -> None:
        # The case the tray's first-column Continue used to drop: a row
        # the stopped fill still OWED (never ran, so its task is
        # abandoned) whose first column was already answered by an
        # earlier fill. Judging owed-ness by that column alone skipped
        # it, and beta stayed blank with no surface able to reach it.
        fill = self._two_output_fill()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        # Row one gets alpha from THIS fill, beta left retryable.
        settle(fill["id"], str(rows[0].id), causes={"beta": StoredCellState.MODEL_ERROR})
        settle(fill["id"], str(rows[1].id), None)
        self.cancel(fill["id"])

        # A refill on beta re-targets row one; stop it before it runs,
        # so row one is that fill's unspent consent.
        second = self.refill(key="beta")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertIn(str(rows[0].id), targeted(second.json()["id"]))
        self.cancel(second.json()["id"])

        # Continue from the TRAY names the fill's first column.
        resumed = self._resume(second.json()["id"], "alpha")
        self.assertEqual(resumed.status_code, 201, resumed.content)
        self.assertIn(str(rows[0].id), targeted(resumed.json()["id"]))

    def test_a_row_every_column_settled_is_not_owed(self) -> None:
        # The union rule must not make rows immortal: a row whose
        # columns are all answered or all settled is done.
        fill = self._two_output_fill()
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        settle(fill["id"], str(rows[0].id), causes={"beta": StoredCellState.NO_EVIDENCE})
        self.cancel(fill["id"])
        resumed = self._resume(fill["id"], "alpha")
        self.assertEqual(resumed.status_code, 201, resumed.content)
        self.assertNotIn(str(rows[0].id), targeted(resumed.json()["id"]))


class ResumeTests(RefillTestCase):
    def test_continue_finishes_only_the_stopped_runs_own_rows(self) -> None:
        # A scoped fill stopped midway resumes ITS remainder, never the
        # column's whole remainder (extend gestures widen; resume does
        # not).
        self.lists.add_rows(self.sheet, [{"company": f"r{n}.io"} for n in range(4)])
        scoped = self.admit(confirmed_row_count=1, rows=1)
        self.cancel(scoped["id"])
        resumed = self.refill_with_resume(scoped["id"])
        self.assertEqual(resumed.status_code, 201, resumed.content)
        body = resumed.json()
        # The stopped 1-row fill left exactly its one pending row.
        self.assertEqual(body["confirmed_row_count"], 1)

    def refill_with_resume(self, fill_run_id: str):
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        return self.client.post(url, {"resume_fill": fill_run_id}, content_type="application/json")

    def test_continue_refuses_after_a_prompt_edit(self) -> None:
        # The config a fill consented under is part of the consent:
        # resuming it with a different prompt would be a different fill
        # wearing its name, so Continue refuses and points at the
        # widening gestures (which run the new prompt).
        scoped = self.admit(confirmed_row_count=1, rows=1)
        self.cancel(scoped["id"])
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(scoped["agent_id"])
        agents.update(agent, config=agent.config().model_copy(update={"prompt": "A sharper ask for {{company}}"}))
        resp = self.refill_with_resume(scoped["id"])
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "config_changed")


class ScopedRefillTests(RefillTestCase):
    def test_scoped_refill_runs_the_next_tranche(self) -> None:
        # A scoped fill covered positions 1-2; the scoped refill takes
        # the NEXT eligible unanswered row only, lands its cutoff on
        # that row's position, and leaves position 4 not-attempted (no
        # outcome row at all).
        self.lists.add_rows(self.sheet, [{"company": "initech.com"}, {"company": "umbrella.io"}])
        fill = self.admit(rows=2)
        first = FillRunWire(**fill)
        self.assertEqual(first.confirmed_row_count, 2)
        settle_all(fill["id"], None)
        self.cancel(fill["id"])

        resp = self.refill(rows=1)
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.confirmed_row_count, 1)
        rows = self.lists.rows_page(self.sheet, after_position=0, limit=10)
        self.assertEqual(targeted_pairs(wire.id), [(str(rows[2].id), 3)])
        self.assertNotIn(4, targeted_positions(wire.id))

    def test_scoped_refill_skips_variable_blank_rows(self) -> None:
        # First N means first N USABLE: the appended variable-blank row
        # never enters the target set, so the scope lands on the
        # eligible row past it.
        fill = self.admit()
        settle_all(fill["id"], None)
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": ""}, {"company": "initech.com"}])

        resp = self.refill(rows=1)
        self.assertEqual(resp.status_code, 201, resp.content)
        wire = FillRunWire(**resp.json())
        self.assertEqual(wire.confirmed_row_count, 1)
        self.assertEqual(targeted_positions(wire.id), [4])

    def test_no_eligible_rows_refuses_with_the_envelope(self) -> None:
        # Unanswered rows remain, but the prompt cannot act on any of
        # them: a distinct refusal from refill_empty, since the fix is
        # filling in values, not accepting a finished column.
        fill = self.admit()
        settle_all(fill["id"], None)
        self.cancel(fill["id"])
        self.lists.add_rows(self.sheet, [{"company": ""}])

        resp = self.refill()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"error": "no_eligible_rows", "detail": "No rows have values for this prompt's variables."},
        )


class RefillNotFoundTests(RefillTestCase):
    def test_foreign_list_reads_as_missing(self) -> None:
        foreign_account = "01AC" + "Z" * 22
        foreign_user = "01US" + "Z" * 22
        foreign_lists = ListService(account_id=foreign_account)
        foreign_sheet = foreign_lists.create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        foreign_lists.add_rows(foreign_sheet, [{"company": "acme.com"}])
        FillAdmissionService(account_id=foreign_account, user_id=foreign_user).admit(
            list_id=str(foreign_sheet.id),
            config=AgentConfig(
                prompt=CONFIG["prompt"],
                provider=CONFIG["provider"],
                source=CONFIG["source"],
                model=CONFIG["model"],
                tools=AgentTools(),
                outputs=[AgentOutput(key="answer", label="Answer", type="text")],
            ),
            confirmed_row_count=1,
        )
        self.assertEqual(self.refill(list_id=str(foreign_sheet.id)).status_code, 404)

    def test_a_column_without_a_fill_is_404(self) -> None:
        # "company" exists but carries no fill member; "missing" does
        # not exist at all. Both read as not-found, not refusals.
        self.assertEqual(self.refill(key="company").status_code, 404)
        self.assertEqual(self.refill(key="missing").status_code, 404)


class RefillLifecycleTests(RefillTestCase):
    def test_refill_after_refill_serializes_through_the_409(self) -> None:
        # Fills on a column accumulate over time but never overlap.
        fill = self.admit()
        self.cancel(fill["id"])
        first = self.refill()
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(FillRunWire(**first.json()).status, FillStatus.PENDING)
        second = self.refill()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"], "fill_active")


class ResumeScopeTests(RefillTestCase):
    """FillTask carries no account of its own: it is reached
    through its fill, which does. Resolving the named fill against THIS
    sheet is what scopes the read."""

    def _refill_resuming(self, fill_run_id: str):
        return self.client.post(
            reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"}),
            {"resume_fill": fill_run_id},
            content_type="application/json",
        )

    def test_a_resume_run_from_another_account_is_refused(self):
        theirs = ListService(account_id="01ACCTOTHERBBBBBBBBBBBBBBB")
        sheet = theirs.create(
            owner_id=TEST_IDENTITY["id"],
            label="Theirs",
            columns=[{"key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        theirs.add_rows(sheet, [{"company": "secret.io"}, {"company": "private.io"}])
        config = AgentConfig(
            prompt=CONFIG["prompt"],
            provider=CONFIG["provider"],
            source=CONFIG["source"],
            model=CONFIG["model"],
            tools=AgentTools(),
            outputs=[AgentOutput(key="answer", label="Answer", type="text")],
        )
        foreign = FillAdmissionService(account_id="01ACCTOTHERBBBBBBBBBBBBBBB", user_id=TEST_IDENTITY["id"]).admit(
            list_id=str(sheet.id), config=config, confirmed_row_count=2
        )
        # Their rows are unsettled, so the only thing standing between
        # this caller and that account's work is the scoping check.
        self.assertEqual(len(targeted(str(foreign.id))), 2)

        mine = self.admit()
        settle_all(mine["id"], StoredCellState.NO_EVIDENCE)
        self.cancel(mine["id"])
        resp = self._refill_resuming(str(foreign.id))
        self.assertEqual(resp.status_code, 400, resp.content)
        # NOT refill_empty: that reads as a true statement about the
        # caller's own sheet, and it would be a lie.
        self.assertEqual(resp.json()["error"], "resume_not_found")

    def test_an_unknown_resume_run_is_refused_the_same_way(self):
        mine = self.admit()
        settle_all(mine["id"], StoredCellState.NO_EVIDENCE)
        self.cancel(mine["id"])
        resp = self._refill_resuming("01M0000000000000000000000X")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "resume_not_found")


class RefillConsentTests(RefillTestCase):
    def test_a_grown_target_refuses_before_spending(self):
        # Refill starts METERED work on a set the server derives at
        # click time. The echo is a spend CEILING: more owed rows than
        # the user reviewed refuses; fewer just fills less.
        fill = self.admit()
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"confirmed_row_count": 1}, content_type="application/json")
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], "row_count_changed")
        # Nothing was opened: the refusal rolls back inside the walk's
        # own transaction.
        self.assertEqual(Fill.objects.filter(list_id=str(self.sheet.id)).count(), 1)

    def test_a_shrunken_target_admits_and_fills_less(self):
        # Reviewed 99, the column owes 2: cheaper than consented, so
        # refusing would be a gate defending nothing.
        fill = self.admit()
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"confirmed_row_count": 99}, content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["confirmed_row_count"], 2)

    def test_a_matching_echo_admits(self):
        fill = self.admit()
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"confirmed_row_count": 2}, content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_a_finished_column_says_EMPTY_even_when_the_caller_echoed(self):
        # The echo used to be checked first, so 0 != N fired and a
        # finished column reported that the SHEET now has 0 rows. It
        # also made NoEligibleRows unreachable for any echoing caller,
        # which is the whole reason the dropped flag exists.
        fill = self.admit()
        settle_all(fill["id"])
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"confirmed_row_count": 2}, content_type="application/json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "refill_empty")

    def test_a_scoped_ask_never_echoes(self):
        # admit skips the echo for a scoped fill because the user was
        # shown no total; refill must follow the same rule, or asking
        # for 50 and finding 2 owed refuses a cheaper answer to the
        # very question that was asked.
        fill = self.admit()
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"rows": 50, "confirmed_row_count": 50}, content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["confirmed_row_count"], 2)

    def test_the_refusal_counts_the_COLUMN_not_the_sheet(self):
        # admit's copy says "the sheet now has N rows"; refill counts
        # what the column still owes, so reusing it told a big sheet it
        # had shrunk to the owed count.
        fill = self.admit()
        self.cancel(fill["id"])
        url = reverse("lists_column_refill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.post(url, {"confirmed_row_count": 1}, content_type="application/json")
        self.assertEqual(resp.status_code, 409, resp.content)
        detail = resp.json()["detail"]
        self.assertIn("2 rows left to fill", detail)
        self.assertNotIn("sheet", detail)

    def test_no_echo_still_admits(self):
        # Optional: resume spends a consent already bought, and every
        # existing caller predates the field.
        fill = self.admit()
        self.cancel(fill["id"])
        self.assertEqual(self.refill().status_code, 201)


class OrphanedColumnTests(RefillTestCase):
    def test_refilling_a_column_the_new_fill_would_not_write_refuses(self):
        # The URL names the column; the CONFIG names what the fill will
        # write. An output renamed since makes them disagree, and the
        # walk would judge done-ness across the old column while the
        # fill owned the new one: every row already answered in the new
        # one re-targeted, each spending a completion that the cell
        # writer then refuses as occupied.
        from agents.services import AgentService
        from openbower_schema.agents import AgentConfig, AgentOutput

        fill = self.admit()
        self.cancel(fill["id"])
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(fill["agent_id"])
        config = AgentConfig(**agent.config().model_dump())
        config.outputs = [AgentOutput(key="renamed", label="Renamed", type="text")]
        agents.update(agent, config=config)

        resp = self.refill()
        # 400, not 404: the column is on the sheet and the user can see
        # it; what changed is what the agent writes.
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "fill_column_retired")
        self.assertIn("no longer writes", resp.json()["detail"])


class ColumnShapeTests(RefillTestCase):
    def test_a_type_change_under_a_live_column_refuses(self):
        # A column's shape is fixed while it exists, the same rule a
        # collision follows. Retyping a column that holds answers makes
        # every later answer TYPE_MISMATCH over data that cannot match;
        # refusing to retype strands it at a type its own output never
        # produces. Refusing says so instead of choosing which way to
        # be wrong.
        from agents.services import AgentService
        from openbower_schema.agents import AgentConfig, AgentOutput

        fill = self.admit()
        self.cancel(fill["id"])
        agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        agent = agents.get_for_fill(fill["agent_id"])
        config = AgentConfig(**agent.config().model_dump())
        config.outputs = [AgentOutput(key="answer", label="Answer", type="number")]
        agents.update(agent, config=config)

        resp = self.refill()
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "column_type_changed")
        self.assertIn("Delete the column", resp.json()["detail"])
        # And it did NOT quietly retype on the way to refusing.
        self.sheet.refresh_from_db()
        self.assertEqual(next(c for c in self.sheet.columns if c["key"] == "answer")["type"], "text")

    def test_an_unchanged_type_refills_normally(self):
        fill = self.admit()
        self.cancel(fill["id"])
        self.assertEqual(self.refill().status_code, 201)


class OutputDriftTests(RefillTestCase):
    """A roster agent's outputs can change between fills, because
    refill re-derives the config on purpose so edits apply. Every
    output the new fill owns has to be a real column by the time it
    opens."""

    def _agent_for(self, fill_run_id: str):
        from agents.models import Agent
        from lists.models import Fill

        return Agent.objects.get(id=Fill.objects.get(id=fill_run_id).agent_id)

    def test_an_output_added_since_the_last_fill_becomes_a_column(self):
        from agents.services import AgentService
        from openbower_schema.agents import AgentConfig, AgentOutput

        fill = self.admit()
        self.cancel(fill["id"])
        agent = self._agent_for(fill["id"])
        config = AgentConfig(**agent.config().model_dump())
        config.outputs = [*config.outputs, AgentOutput(key="phantom", label="Phantom", type="text")]
        AgentService(account_id=agent.account_id).update(agent, config=config)

        resp = self.refill()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.sheet.refresh_from_db()
        keys = [column["key"] for column in self.sheet.columns]
        # Without the shared claim, the fill owned "phantom" while the
        # sheet had no such column: every row spent a completion and
        # wrote a cell no surface renders.
        self.assertIn("phantom", keys)
        self.assertIn("phantom", resp.json()["column_keys"])
        appended = next(column for column in self.sheet.columns if column["key"] == "phantom")
        self.assertEqual(appended["fill"], {"agent_id": str(agent.id), "current_fill_id": resp.json()["id"]})

    def test_a_refill_still_runs_over_its_own_answered_column(self):
        # The collision rule must not read a fill's OWN previous
        # answers as somebody else's data: the column it refills is
        # occupied by construction.
        from lists.tests.fill_helpers import settle_all

        fill = self.admit()
        settle_all(fill["id"])
        # Terminal, so the refusal under test is the column rule and
        # not the one-live-fill-per-column gate.
        self.cancel(fill["id"])
        resp = self.refill()
        # Every row answered, so this refuses as EMPTY (nothing owed),
        # never as a column collision.
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "refill_empty")


class ChildAccountTests(RefillTestCase):
    """Every writer must stamp the account. A missed one stores "" in
    silence (CharField's empty is its implicit default), so nothing
    but an explicit assertion catches it."""

    def test_every_fill_child_carries_its_account(self):
        account = TEST_IDENTITY["account_id"]
        fill = self.admit()
        settle_all(fill["id"], StoredCellState.NO_EVIDENCE)
        outcomes = FillTask.objects.filter(fill_run_id=fill["id"])
        cells = FillCellState.objects.filter(list_id=str(self.sheet.id))
        self.assertTrue(outcomes.exists() and cells.exists())
        self.assertEqual({o.account_id for o in outcomes}, {account})
        self.assertEqual({c.account_id for c in cells}, {account})

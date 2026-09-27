"""An agent's output set is fixed while its columns are on a sheet: the
columns ARE the outputs' shape, and a fill only writes the columns that
exist. Through the real agents endpoint, over a real sheet whose AI
column the agent fills (model_for patched, the lists tests' seam).

Run: DJANGO_ENV=test uv run python manage.py test agents.tests.test_output_lock
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from lists.services.ai_columns import AiColumnService
from lists.services.columns import ColumnService
from lists.services.lists import ListService
from lists.services.workflows import WorkflowService

ACCOUNT = TEST_IDENTITY["account_id"]
USER = TEST_IDENTITY["id"]

OUTPUT = {"key": "answer", "label": "Answer", "type": "text"}
EMAIL = {"key": "email", "label": "Email", "type": "email"}
CONFIG = {
    "prompt": "Find the buyer at {{company}}",
    "provider": "openai_compatible",
    "source": "local",
    "model": "gemma4:12b",
    "tools": {},
    "outputs": [OUTPUT],
}


class OutputLockTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        patcher = patch("lists.services.runnable.model_for")
        patcher.start()
        self.addCleanup(patcher.stop)
        created = self.client.post(
            reverse("agents_index"), {"label": "Finder", "config": CONFIG}, content_type="application/json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.agent_id = created.json()["id"]
        self.lists = ListService(account_id=ACCOUNT)
        self.sheet = self.lists.create(
            owner_id=USER,
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(self.sheet.id), agent_id=self.agent_id)

    def patch_outputs(self, outputs: list[dict], **config):
        body = {"config": {**CONFIG, **config, "outputs": outputs}}
        url = reverse("agents_detail", kwargs={"id": self.agent_id})
        return self.client.patch(url, body, content_type="application/json")

    def stored_outputs(self) -> list[tuple[str, str]]:
        agent = Agent.objects.get(id=self.agent_id)
        return [(output["key"], output["type"]) for output in agent.outputs]

    def test_an_output_added_to_a_used_agent_refuses_with_the_envelope(self) -> None:
        # FAILS if the save lands outputs the sheet's columns do not have.
        resp = self.patch_outputs([OUTPUT, {"key": "email", "label": "Email", "type": "email"}])
        self.assertEqual(resp.status_code, 409, resp.content)
        body = resp.json()
        self.assertEqual(body["error"], "agent_outputs_in_use")
        self.assertIn("Prospects", body["detail"])
        self.assertEqual(self.stored_outputs(), [("answer", "text")])

    def _two_output_agent(self) -> str:
        """A second roster agent with two outputs, its columns on the
        sheet: the fixture for the changes a one-output agent cannot
        express (a removal empties the list, which the serializer
        refuses before the guard; a reorder needs two)."""
        config = {**CONFIG, "outputs": [OUTPUT, EMAIL]}
        created = self.client.post(
            reverse("agents_index"), {"label": "Pair", "config": config}, content_type="application/json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        agent_id = created.json()["id"]
        other = self.lists.create(owner_id=USER, label="Accounts", columns=[], origin="manual")
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(other.id), agent_id=agent_id)
        return agent_id

    def _patch_agent(self, agent_id: str, outputs: list[dict]):
        url = reverse("agents_detail", kwargs={"id": agent_id})
        return self.client.patch(url, {"config": {**CONFIG, "outputs": outputs}}, content_type="application/json")

    def test_an_output_removed_from_a_used_agent_refuses(self) -> None:
        # Removal is the change that leaves a column no output backs.
        # FAILS if the guard refuses only additions and changes.
        agent_id = self._two_output_agent()
        resp = self._patch_agent(agent_id, [OUTPUT])
        self.assertEqual(resp.status_code, 409, resp.content)
        stored = [(o["key"], o["type"]) for o in Agent.objects.get(id=agent_id).outputs]
        self.assertEqual(stored, [("answer", "text"), ("email", "email")])

    def test_two_or_more_sheets_are_counted_never_listed(self) -> None:
        # One sheet is named (the base fixture's "Prospects"); two or
        # more are a count, so the refusal stays one short sentence in a
        # toast however many sheets use the agent. FAILS if the copy
        # lists names.
        agent_id = self._two_output_agent()
        third = self.lists.create(owner_id=USER, label="Targets", columns=[], origin="manual")
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(third.id), agent_id=agent_id)
        resp = self._patch_agent(agent_id, [OUTPUT])
        self.assertEqual(resp.status_code, 409, resp.content)
        detail = resp.json()["detail"]
        self.assertIn("on 2 sheets.", detail)
        self.assertNotIn("Accounts", detail)
        self.assertNotIn("Targets", detail)

    def test_reordering_a_used_agents_outputs_is_allowed(self) -> None:
        # Order is not part of the columns' shape (a column is ordered
        # on its sheet). FAILS if the comparison is order-sensitive.
        agent_id = self._two_output_agent()
        resp = self._patch_agent(agent_id, [EMAIL, OUTPUT])
        self.assertEqual(resp.status_code, 200, resp.content)
        stored = [(o["key"], o["type"]) for o in Agent.objects.get(id=agent_id).outputs]
        self.assertEqual(stored, [("email", "email"), ("answer", "text")])

    def test_a_retyped_or_rekeyed_output_refuses(self) -> None:
        self.assertEqual(self.patch_outputs([{**OUTPUT, "type": "number"}]).status_code, 409)
        self.assertEqual(self.patch_outputs([{**OUTPUT, "key": "buyer"}]).status_code, 409)
        self.assertEqual(self.stored_outputs(), [("answer", "text")])

    def test_what_fills_the_columns_stays_editable(self) -> None:
        # The prompt, the model, and an output's label change how the
        # columns are filled, never what they hold. FAILS if the guard
        # compares more than each output's key and type.
        resp = self.patch_outputs([{**OUTPUT, "label": "Buyer"}], prompt="Name the buyer at {{company}}", model="x")
        self.assertEqual(resp.status_code, 200, resp.content)
        agent = Agent.objects.get(id=self.agent_id)
        self.assertEqual(
            (agent.prompt, agent.model, agent.outputs[0]["label"]),
            (
                "Name the buyer at {{company}}",
                "x",
                "Buyer",
            ),
        )

    def test_deleting_the_columns_frees_the_outputs(self) -> None:
        # A node whose columns were all deleted uses nothing.
        ColumnService(account_id=ACCOUNT, user_id=USER).delete(str(self.sheet.id), key="answer")
        resp = self.patch_outputs([OUTPUT, {"key": "email", "label": "Email", "type": "email"}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.stored_outputs(), [("answer", "text"), ("email", "email")])

    def test_the_uses_name_every_sheet_in_three_reads(self) -> None:
        other = self.lists.create(owner_id=USER, label="Accounts", columns=[], origin="manual")
        AiColumnService(account_id=ACCOUNT, user_id=USER).add(str(other.id), agent_id=self.agent_id)
        with self.assertNumQueries(3):
            uses = WorkflowService(account_id=ACCOUNT).agent_column_uses(self.agent_id)
        self.assertEqual(
            sorted((use.label, use.column_keys) for use in uses),
            [("Accounts", ("answer",)), ("Prospects", ("answer",))],
        )

    def test_an_agent_on_no_sheet_edits_freely(self) -> None:
        free = AgentService(account_id=ACCOUNT).create(
            owner_id=USER, label="Unused", config=Agent.objects.get(id=self.agent_id).config()
        )
        resp = self.client.patch(
            reverse("agents_detail", kwargs={"id": str(free.id)}),
            {"config": {**CONFIG, "outputs": [{"key": "email", "label": "Email", "type": "email"}]}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

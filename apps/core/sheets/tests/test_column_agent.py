"""The agent behind an AI column, read and edited from the column
(GET and PATCH .../columns/{key}/agent), through the endpoint with real
cookie auth (the IdP mocked at its httpx boundary via the shared login
helper). model_for is the one patched seam, per the fill views'
precedent.

Run: DJANGO_ENV=test uv run python manage.py test sheets.tests.test_column_agent
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from agents.providers import ModelUnavailable
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import PROMPT_MAX_LENGTH, AgentConfig, AgentOutput, AgentTools
from sheets.rows import rows_without_external_ids

from ..services.sheets import SheetService
from .fill_helpers import post_ai_column

# Request-shaped config (the serializer derives the output key).
CONFIG = {
    "prompt": "Find the answer for {{company}}",
    "provider": "openai_compatible",
    "source": "ollama",
    "model": "test-model",
    "tools": {},
    "outputs": [{"label": "Answer", "type": "text"}],
}


def edited(**changes) -> dict:
    """The column's config as an edit sends it: the stored key rides
    along, so the output set is the column's own."""
    body = {**CONFIG, "outputs": [{"key": "answer", "label": "Answer", "type": "text"}]}
    body.update(changes)
    return body


class ColumnAgentTestCase(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.agents = AgentService(account_id=TEST_IDENTITY["account_id"])
        self.sheets = SheetService(account_id=TEST_IDENTITY["account_id"])
        self.sheet = self.sheets.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        self.sheets.add_rows(
            self.sheet, rows_without_external_ids([{"company": "acme.com"}, {"company": "example.io"}])
        )
        patcher = patch("sheets.services.runnable.model_for")
        self.model_for = patcher.start()
        self.addCleanup(patcher.stop)

    def add_column(self, agent_id: str = "") -> str:
        """Create the AI column (no fill: an edit needs only the column);
        returns the id of the agent behind it."""
        body: dict = {"agent_id": agent_id} if agent_id else {"config": CONFIG}
        resp = post_ai_column(self.client, str(self.sheet.id), body)
        self.assertEqual(resp.status_code, 201, resp.content)
        return agent_id or str(Agent.objects.get(ephemeral=True).id)

    def add_saved_column(self) -> Agent:
        saved = self.agents.create(
            owner_id=TEST_IDENTITY["id"],
            label="Answerer",
            config=AgentConfig(
                prompt=CONFIG["prompt"],
                provider=CONFIG["provider"],
                source=CONFIG["source"],
                model=CONFIG["model"],
                tools=AgentTools(),
                outputs=[AgentOutput(key="answer", label="Answer", type="text")],
            ),
        )
        self.add_column(agent_id=str(saved.id))
        return saved

    def url(self, *, sheet_id: str = "", key: str = "answer") -> str:
        return reverse("sheets_column_agent", kwargs={"id": sheet_id or str(self.sheet.id), "key": key})

    def patch_agent(self, body: dict, *, sheet_id: str = "", key: str = "answer"):
        return self.client.patch(self.url(sheet_id=sheet_id, key=key), body, content_type="application/json")


class ColumnAgentReadTests(ColumnAgentTestCase):
    def test_a_column_owned_agent_reads_with_its_whole_config(self) -> None:
        agent_id = self.add_column()
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual((body["agent_id"], body["saved"]), (agent_id, False))
        self.assertEqual(body["config"]["prompt"], CONFIG["prompt"])
        self.assertEqual(body["config"]["model"], CONFIG["model"])
        self.assertEqual([output["key"] for output in body["config"]["outputs"]], ["answer"])

    def test_a_saved_agent_reads_as_saved(self) -> None:
        saved = self.add_saved_column()
        body = self.client.get(self.url()).json()
        self.assertEqual((body["agent_id"], body["agent_label"], body["saved"]), (str(saved.id), "Answerer", True))


class ColumnAgentEditTests(ColumnAgentTestCase):
    def test_an_edit_writes_the_whole_config(self) -> None:
        agent_id = self.add_column()
        resp = self.patch_agent(
            edited(
                prompt="Reworded ask for {{company}}",
                model="other-model",
                tools={"web_search": True},
                outputs=[{"key": "answer", "label": "Answer", "type": "text", "description": "One line"}],
            )
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["config"]["prompt"], "Reworded ask for {{company}}")
        agent = Agent.objects.get(id=agent_id)
        self.assertEqual((agent.prompt, agent.model), ("Reworded ask for {{company}}", "other-model"))
        self.assertTrue(agent.tools["web_search"])
        self.assertEqual(agent.outputs[0]["description"], "One line")

    def test_a_saved_agent_is_refused(self) -> None:
        saved = self.add_saved_column()
        resp = self.patch_agent(edited(prompt="Sharper ask for {{company}}"))
        # FAILS if the sheet can edit an agent other sheets may share.
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], "agent_saved")
        saved.refresh_from_db()
        self.assertEqual(saved.prompt, CONFIG["prompt"])

    def test_a_changed_output_set_is_refused(self) -> None:
        self.add_column()
        added = edited(
            outputs=[{"key": "answer", "label": "Answer", "type": "text"}, {"label": "Extra", "type": "text"}]
        )
        retyped = edited(outputs=[{"key": "answer", "label": "Answer", "type": "url"}])
        for body in (added, retyped):
            resp = self.patch_agent(body)
            self.assertEqual(resp.status_code, 409, resp.content)
            self.assertEqual(resp.json()["error"], "agent_outputs_in_use")

    def test_an_unrunnable_model_is_refused(self) -> None:
        agent_id = self.add_column()
        self.model_for.side_effect = ModelUnavailable("no model named gone-model")
        resp = self.patch_agent(edited(model="gone-model"))
        # FAILS if an edit can store a config no fill can run.
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], "model_unrunnable")
        self.assertEqual(Agent.objects.get(id=agent_id).model, CONFIG["model"])

    def test_an_edit_fixes_a_retired_provider(self) -> None:
        agent_id = self.add_column()
        Agent.objects.filter(id=agent_id).update(provider="retired_spec")
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        resp = self.patch_agent(edited())
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Agent.objects.get(id=agent_id).provider, CONFIG["provider"])

    def test_an_edit_during_a_live_fill_is_accepted(self) -> None:
        # A fill reads its agent live, so the edit reaches the running
        # fill's next row.
        agent_id = self.add_column()
        fill_url = reverse("sheets_column_fill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        started = self.client.post(fill_url, {}, content_type="application/json")
        self.assertEqual(started.status_code, 201, started.content)
        resp = self.patch_agent(edited(prompt="Edited mid-fill for {{company}}"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Agent.objects.get(id=agent_id).prompt, "Edited mid-fill for {{company}}")

    def test_bad_prompts_are_refused(self) -> None:
        self.add_column()
        for prompt in ("", "p" * (PROMPT_MAX_LENGTH + 1), "Broken {% if %}"):
            self.assertEqual(self.patch_agent(edited(prompt=prompt)).status_code, 400, prompt[:20])

    def test_unknown_sheet_column_and_fill_less_column_read_as_missing(self) -> None:
        self.add_column()
        self.assertEqual(self.patch_agent(edited(), sheet_id="01LZ" + "Z" * 22).status_code, 404)
        # "company" exists but carries no fill member; "missing" does
        # not exist at all. Both read as not-found, not refusals.
        self.assertEqual(self.patch_agent(edited(), key="company").status_code, 404)
        self.assertEqual(self.patch_agent(edited(), key="missing").status_code, 404)
        self.assertEqual(self.client.get(self.url(key="missing")).status_code, 404)

    def test_foreign_sheet_reads_as_missing(self) -> None:
        foreign_sheets = SheetService(account_id="01AC" + "Z" * 22)
        foreign_sheet = foreign_sheets.create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        self.assertEqual(self.patch_agent(edited(), sheet_id=str(foreign_sheet.id)).status_code, 404)

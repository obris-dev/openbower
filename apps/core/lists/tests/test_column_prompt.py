"""The column-scoped prompt edit (PATCH .../columns/{key}/prompt),
through the endpoint with real cookie auth (the IdP mocked at its
httpx boundary via the shared login helper). model_for is the one
patched seam, per the fill views' precedent.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_column_prompt
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from agents.services import AgentService
from common.testing import TEST_IDENTITY, login_session
from openbower_schema.agents import PROMPT_MAX_LENGTH, AgentConfig, AgentOutput, AgentTools

from ..services.lists import ListService
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


class ColumnPromptTestCase(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.agents = AgentService(account_id=TEST_IDENTITY["account_id"])
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

    def add_column(self, agent_id: str = "") -> str:
        """Create the AI column (no fill: an edit needs only the column);
        returns the id of the agent behind it."""
        body: dict = {"agent_id": agent_id} if agent_id else {"config": CONFIG}
        resp = post_ai_column(self.client, str(self.sheet.id), body)
        self.assertEqual(resp.status_code, 201, resp.content)
        return agent_id or str(Agent.objects.get(ephemeral=True).id)

    def put_prompt(self, prompt: str, *, list_id: str = "", key: str = "answer"):
        url = reverse("lists_column_prompt", kwargs={"id": list_id or str(self.sheet.id), "key": key})
        return self.client.patch(url, {"prompt": prompt}, content_type="application/json")


class ColumnPromptTests(ColumnPromptTestCase):
    def test_get_reads_the_columns_current_config(self) -> None:
        # Surfaces peeking at "what fills this column" read the CURRENT
        # config here, the one a fill reads live.
        self.add_column()
        url = reverse("lists_column_prompt", kwargs={"id": str(self.sheet.id), "key": "answer"})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            resp.json(), {"prompt": CONFIG["prompt"], "model": CONFIG["model"], "source": CONFIG["source"]}
        )

    def test_edit_round_trips_onto_the_ephemeral_agent(self) -> None:
        agent_id = self.add_column()
        resp = self.put_prompt("Reworded ask for {{company}}")
        self.assertEqual(resp.status_code, 200, resp.content)
        # The echo is the column's CURRENT fill config: the new prompt
        # plus the untouched model address.
        self.assertEqual(
            resp.json(),
            {"prompt": "Reworded ask for {{company}}", "model": CONFIG["model"], "source": CONFIG["source"]},
        )
        agent = Agent.objects.get(id=agent_id)
        self.assertEqual(agent.prompt, "Reworded ask for {{company}}")
        # ONLY the prompt moved: the rest of the config round-tripped.
        self.assertEqual(agent.model, CONFIG["model"])
        self.assertEqual([output["key"] for output in agent.outputs], ["answer"])

    def test_a_roster_agents_column_is_editable_too(self) -> None:
        # The column is the custody path either way; the builder stays
        # the roster's full editor.
        roster = self.agents.create(
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
        self.add_column(agent_id=str(roster.id))
        resp = self.put_prompt("Sharper ask for {{company}}")
        self.assertEqual(resp.status_code, 200, resp.content)
        roster.refresh_from_db()
        self.assertEqual(roster.prompt, "Sharper ask for {{company}}")

    def test_an_edit_during_a_live_fill_is_accepted(self) -> None:
        # A fill reads its agent live, so the edit reaches the running
        # fill's next row; the server accepts, and the CLIENT disables
        # the affordance to avoid mixing two asks in one fill.
        agent_id = self.add_column()
        fill_url = reverse("lists_column_fill", kwargs={"id": str(self.sheet.id), "key": "answer"})
        started = self.client.post(fill_url, {}, content_type="application/json")
        self.assertEqual(started.status_code, 201, started.content)
        resp = self.put_prompt("Edited mid-fill for {{company}}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Agent.objects.get(id=agent_id).prompt, "Edited mid-fill for {{company}}")

    def test_prompt_over_the_contract_bound_refuses(self) -> None:
        self.add_column()
        resp = self.put_prompt("p" * (PROMPT_MAX_LENGTH + 1))
        self.assertEqual(resp.status_code, 400)

    def test_blank_prompt_refuses(self) -> None:
        self.add_column()
        self.assertEqual(self.put_prompt("").status_code, 400)

    def test_retired_provider_refuses_with_the_envelope(self) -> None:
        # update() persists the whole config, so acting through a
        # coerced substitute spec would silently rewrite the provider.
        agent_id = self.add_column()
        Agent.objects.filter(id=agent_id).update(provider="retired_spec")
        resp = self.put_prompt("New ask for {{company}}")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "provider_retired")

    def test_unknown_list_column_and_fill_less_column_read_as_missing(self) -> None:
        self.add_column()
        self.assertEqual(self.put_prompt("x", list_id="01LZ" + "Z" * 22).status_code, 404)
        # "company" exists but carries no fill member; "missing" does
        # not exist at all. Both read as not-found, not refusals.
        self.assertEqual(self.put_prompt("x", key="company").status_code, 404)
        self.assertEqual(self.put_prompt("x", key="missing").status_code, 404)

    def test_foreign_list_reads_as_missing(self) -> None:
        foreign_lists = ListService(account_id="01AC" + "Z" * 22)
        foreign_sheet = foreign_lists.create(
            owner_id=TEST_IDENTITY["id"], label="Not yours", columns=[], origin="manual"
        )
        self.assertEqual(self.put_prompt("x", list_id=str(foreign_sheet.id)).status_code, 404)

"""The agent atom: CRUD, caps, isolation, and the wire shapes. Session
auth is real (the IdP mocked at its httpx boundary via the shared login
helper).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from agents.models import Agent
from common.testing import TEST_IDENTITY, login_session

CONFIG = {
    "prompt": "Find the buyer at {{name}}",
    "provider": "openai_compatible",
    "source": "local",
    "model": "gemma4:12b",
    "tools": {},
    "outputs": [{"label": "Answer", "type": "text", "description": "The buyer's name"}],
}


class AgentCrudTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def _create(self, **overrides) -> dict:
        body = {"label": "Decision makers", "config": {**CONFIG, **overrides}}
        resp = self.client.post(reverse("agents_index"), body, content_type="application/json")
        assert resp.status_code == 201, resp.content
        return resp.json()

    def test_crud_roundtrip(self):
        created = self._create(tools={"find_contacts": True})
        self.assertEqual(created["config"]["tools"], {"web_search": False, "find_contacts": True})
        self.assertEqual(created["config"]["outputs"][0]["key"], "answer")

        listing = self.client.get(reverse("agents_index")).json()
        self.assertEqual([a["label"] for a in listing["items"]], ["Decision makers"])

        renamed = self.client.patch(
            reverse("agents_detail", kwargs={"id": created["id"]}),
            {"label": "Renamed"},
            content_type="application/json",
        )
        self.assertEqual(renamed.json()["label"], "Renamed")
        # A label-only PATCH must not disturb the config.
        self.assertEqual(renamed.json()["config"]["prompt"], CONFIG["prompt"])

        gone = self.client.delete(reverse("agents_detail", kwargs={"id": created["id"]}))
        self.assertEqual(gone.status_code, 204)
        self.assertEqual(Agent.objects.count(), 0)

    def test_outputs_are_required(self):
        resp = self.client.post(
            reverse("agents_index"),
            {"label": "No outputs", "config": {**CONFIG, "outputs": []}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_output_keys_rejected(self):
        resp = self.client.post(
            reverse("agents_index"),
            {
                "label": "Dupes",
                "config": {
                    **CONFIG,
                    "outputs": [{"label": "Name", "type": "text"}, {"label": "name!", "type": "text"}],
                },
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_provider_rejected(self):
        resp = self.client.post(
            reverse("agents_index"),
            {"label": "Bad door", "config": {**CONFIG, "provider": "mystery"}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_roster_cap_counts_configured_rows_only(self):
        with patch("agents.services.agents.MAX_AGENTS", 2):
            self._create()
            # Ephemeral rows never count against the roster.
            Agent.objects.create(
                account_id=TEST_IDENTITY["account_id"],
                user_id=TEST_IDENTITY["id"],
                label="hidden",
                provider="openai_compatible",
                source="local",
                model="m",
                prompt="p",
                outputs=[{"key": "a", "label": "A", "type": "text", "description": ""}],
                ephemeral=True,
            )
            self._create()
            resp = self.client.post(
                reverse("agents_index"), {"label": "Third", "config": CONFIG}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, 400)

    def test_ephemeral_rows_hidden_from_roster(self):
        Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="quick",
            provider="openai_compatible",
            model="m",
            prompt="p",
            outputs=[{"key": "a", "label": "A", "type": "text", "description": ""}],
            ephemeral=True,
        )
        listing = self.client.get(reverse("agents_index")).json()
        self.assertEqual(listing["items"], [])

    def test_foreign_account_reads_as_missing(self):
        created = self._create()
        Agent.objects.filter(id=created["id"]).update(account_id="01AC" + "Z" * 22)
        resp = self.client.get(reverse("agents_detail", kwargs={"id": created["id"]}))
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get(reverse("agents_index")).status_code, 401)


class PromptSyntaxTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_a_prompt_the_engine_cannot_parse_refuses_at_save(self):
        # Template syntax is CONFIG: it 400s at the boundary, never as
        # a per-row surprise.
        resp = self.client.post(
            reverse("agents_index"),
            {"label": "Broken", "config": {**CONFIG, "prompt": "{% if %}unclosed"}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("prompt template error", str(resp.json()))


class PatchAndBoundsTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.agent_id = self.client.post(
            reverse("agents_index"),
            {"label": "Finder", "config": CONFIG},
            content_type="application/json",
        ).json()["id"]

    def test_an_empty_patch_refuses(self):
        resp = self.client.patch(reverse("agents_detail", args=[self.agent_id]), {}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nothing to change", str(resp.json()))

    def test_wire_bounds_refuse_at_the_door(self):
        from agents.constants import LABEL_MAX_LENGTH, MAX_AGENT_OUTPUTS, PROMPT_MAX_LENGTH

        cases = [
            {"label": "x" * (LABEL_MAX_LENGTH + 1), "config": CONFIG},
            {"label": "x", "config": {**CONFIG, "prompt": "p" * (PROMPT_MAX_LENGTH + 1)}},
            {
                "label": "x",
                "config": {
                    **CONFIG,
                    "outputs": [{"label": f"Out {i}", "type": "text"} for i in range(MAX_AGENT_OUTPUTS + 1)],
                },
            },
        ]
        for body in cases:
            resp = self.client.post(reverse("agents_index"), body, content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_an_ephemeral_row_is_not_addressable_through_the_crud(self):
        # Column-owned rows are phase 5's custody; the roster's CRUD
        # must neither read, rewrite, nor delete them.
        from agents.models import Agent

        row = Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="quick",
            provider="openai_compatible",
            source="local",
            model="m",
            prompt="p",
            tools={"web_search": False, "find_contacts": False},
            outputs=[{"key": "a", "label": "A", "type": "text", "description": ""}],
            ephemeral=True,
        )
        for method, body in (("get", None), ("patch", {"label": "renamed"}), ("delete", None)):
            resp = getattr(self.client, method)(
                reverse("agents_detail", args=[str(row.id)]),
                *((body,) if body else ()),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 404, method)


class StoredConfigCoercionTests(TestCase):
    """The read a stored row can never 500. Both custodies share it:
    the agent row, and the fill's frozen snapshot."""

    def test_a_provider_the_enum_no_longer_knows_is_trailed(self):
        # The substitution used to happen BEFORE the coercion, so its
        # warning compared a value to itself and the one case it exists
        # to trail went silent.
        from agents.coercion import coerce_config

        stored = {
            "prompt": "p",
            "provider": "a_spec_this_version_retired",
            "source": "s",
            "model": "m",
            "tools": {},
            "outputs": [{"key": "a", "label": "A", "type": "text"}],
        }
        with self.assertLogs("agents.coercion", level="WARNING") as caught:
            config = coerce_config(stored, origin="probe")
        self.assertEqual(config.provider, "openai_compatible")
        self.assertTrue(any("clamped/coerced" in line for line in caught.output))

    def test_a_stored_blob_that_is_not_an_object_still_reads(self):
        # `or {}` rescues only a FALSY blob, so a truthy non-dict
        # reached .get and raised, which is the permanent 500 on the
        # fills page this module exists to prevent.
        from agents.coercion import coerce_config

        for blob in ([1, 2, 3], "a string", 7):
            with self.subTest(blob=type(blob).__name__), self.assertLogs("agents.coercion", level="WARNING"):
                self.assertEqual(coerce_config(blob, origin="probe").outputs[0].key, "unreadable_output")

    def test_a_stored_null_clamps_to_blank_never_the_word_None(self):
        from agents.coercion import coerce_config

        config = coerce_config(
            {"source": None, "model": None, "outputs": [{"key": "a", "label": "A", "type": "text"}]},
            origin="probe",
        )
        self.assertEqual(config.source, "")
        self.assertEqual(config.model, "")

    def test_a_null_output_key_or_label_clamps_too(self):
        # These matter MORE than the scalars: a key becomes a COLUMN
        # KEY and a label a sheet header, and nothing downstream
        # refuses the literal "None" (the contract bounds their length,
        # not their shape).
        from agents.coercion import coerce_config

        config = coerce_config(
            {"outputs": [{"key": None, "label": None, "type": "text", "description": None}]},
            origin="probe",
        )
        self.assertEqual(config.outputs[0].key, "")
        self.assertEqual(config.outputs[0].label, "")
        self.assertEqual(config.outputs[0].description, "")


class ListWireTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_the_list_ships_slim_rows_never_whole_configs(self):
        # The index renders four columns; a full config per row would
        # ship every prompt on every visit (the edit page fetches by id).
        self.client.post(
            reverse("agents_index"),
            {"label": "Finder", "config": CONFIG},
            content_type="application/json",
        )
        item = self.client.get(reverse("agents_index")).json()["items"][0]
        self.assertNotIn("config", item)
        self.assertEqual(item["model"], CONFIG["model"])
        self.assertEqual(set(item["tools"]), {"web_search", "find_contacts"})


class ReadClampTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_a_stored_row_over_any_max_renders_clamped_never_500s(self):
        # The contract's max bounds validate on READ (config() rebuilds
        # AgentConfig from the row): a future producer's oversize row,
        # or a bound tightened post-release, must render, not brick
        # the GET (a bound can tighten after rows exist under the old one).
        from agents.constants import MAX_AGENT_OUTPUTS, OUTPUT_KEY_MAX_LENGTH, PROMPT_MAX_LENGTH
        from agents.models import Agent

        row = Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="Legacy",
            provider="openai_compatible",
            source="local",
            model="m",
            prompt="p" * (PROMPT_MAX_LENGTH + 8),
            tools={"web_search": False, "find_contacts": False},
            outputs=[{"key": "k" * (OUTPUT_KEY_MAX_LENGTH + 8), "label": "A", "type": "text", "description": ""}]
            + [{"key": f"k{i}", "label": "B", "type": "text", "description": ""} for i in range(MAX_AGENT_OUTPUTS + 2)],
        )
        resp = self.client.get(reverse("agents_detail", args=[str(row.id)]))
        self.assertEqual(resp.status_code, 200)
        config = resp.json()["config"]
        self.assertEqual(len(config["prompt"]), PROMPT_MAX_LENGTH)
        self.assertEqual(len(config["outputs"]), MAX_AGENT_OUTPUTS)
        self.assertEqual(len(config["outputs"][0]["key"]), OUTPUT_KEY_MAX_LENGTH)

    def test_retired_enum_values_coerce_on_read(self):
        # Lengths are only half the clamp: enum members can retire
        # while rows still hold them, and a stored one must render
        # (type falls to text; a retired provider renders under the
        # first spec, reads as vanished in the picker, and refuses at
        # run time).
        from agents.models import Agent

        row = Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="Retired",
            provider="retired_llm_spec",
            source="local",
            model="m",
            prompt="p",
            tools={"web_search": False, "find_contacts": False},
            outputs=[{"key": "pic", "label": "Pic", "type": "image", "description": ""}],
        )
        resp = self.client.get(reverse("agents_detail", args=[str(row.id)]))
        self.assertEqual(resp.status_code, 200)
        config = resp.json()["config"]
        self.assertEqual(config["outputs"][0]["type"], "text")
        self.assertEqual(config["provider"], "openai_compatible")
        # With no same-named source under the substitute spec (this
        # profile configures none), the coerced address refuses at run
        # time; a deploy that names one identically WOULD run there
        # (the render-over-refuse trade, stated at the coercion).
        from agents.providers import ModelUnavailable, model_for

        with self.assertRaises(ModelUnavailable):
            model_for(config["provider"], config["source"], config["model"])

    def test_junk_storage_shapes_still_render(self):
        # Non-dict tools and an all-junk outputs list are the clamp's
        # last residuals: the GET must render, not 500.
        from agents.models import Agent

        row = Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="Junk",
            provider="openai_compatible",
            source="local",
            model="m",
            prompt="p",
            tools="not-a-dict",
            outputs=["not-a-dict", 7],
        )
        resp = self.client.get(reverse("agents_detail", args=[str(row.id)]))
        self.assertEqual(resp.status_code, 200)
        config = resp.json()["config"]
        self.assertEqual(config["tools"], {"web_search": False, "find_contacts": False})
        self.assertEqual(len(config["outputs"]), 1)
        # BOTH wire legs: one junk row must not brick the whole
        # unpaged roster GET either, and junk VALUES sanitize too.
        row.tools = {"web_search": []}
        row.save(update_fields=["tools"])
        listing = self.client.get(reverse("agents_index"))
        self.assertEqual(listing.status_code, 200)
        item = next(i for i in listing.json()["items"] if i["id"] == str(row.id))
        self.assertEqual(item["tools"], {"web_search": False, "find_contacts": False})

    def test_unknown_tool_keys_in_storage_read_tolerantly(self):
        # The read path must survive a key set from ANOTHER version
        # (the exact hazard: a server that adds a tool, a browser
        # holding the old bundle, or a row written by a newer server).
        from agents.models import Agent

        row = Agent.objects.create(
            account_id=TEST_IDENTITY["account_id"],
            user_id=TEST_IDENTITY["id"],
            label="Future",
            provider="openai_compatible",
            source="local",
            model="m",
            prompt="p",
            tools={"web_search": True, "find_contacts": False, "crystal_ball": True},
            outputs=[{"key": "a", "label": "A", "type": "text", "description": ""}],
        )
        resp = self.client.get(reverse("agents_detail", args=[str(row.id)]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()["config"]["tools"]), {"web_search", "find_contacts"})


class ReservedOutputKeyTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_a_key_colliding_with_the_answer_models_attributes_refuses(self):
        # pydantic silently SWALLOWS a model_config field (a permanently
        # blank column with zero diagnosis); the boundary refuses it.
        resp = self.client.post(
            reverse("agents_index"),
            {"label": "x", "config": {**CONFIG, "outputs": [{"label": "Model Config", "type": "text"}]}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reserved", str(resp.json()))

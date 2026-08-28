"""The catalog's honesty (only runnable models) and the runtime's
grounding doctrine. Mocking sits at the seams WE own: search and roster
probes at httpx, the model as a scripted FunctionModel through
model_for (the framework's wire is not ours to test).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import httpx
from ddgs.exceptions import TimeoutException as DDGSTimeout
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.constants import (
    MAX_TOOL_CALLS,
    MODEL_MAX_LENGTH,
    PROBE_FAILURE_TTL_SECONDS,
    SEARCH_BACKOFF_SECONDS,
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_RUN_MAX_AGE_SECONDS,
    TEST_RUN_STALE_PENDING_SECONDS,
    TEST_RUN_WORST_CASE_SECONDS,
    TEST_VALUE_MAX_LENGTH,
    TestRunStatus,
)
from agents.models import AgentTestRun
from agents.providers import anthropic_compatible, openai_compatible
from common.testing import TEST_IDENTITY, FakeResponse, login_session
from openbower_kernel.fields import min_ulid_at
from openbower_kernel.provider_config import ProviderSpec
from openbower_schema.agents import CONFIDENCE_SUFFIX

from .sources import source

_SERP = {
    "tasks": [
        {
            "status_code": 20000,
            "result": [
                {
                    "items": [
                        {
                            "type": "organic",
                            "title": "Jane Doe - VP of Sales - Acme | LinkedIn",
                            "url": "https://www.linkedin.com/in/janedoe",
                            "description": "Jane Doe. VP of Sales at Acme.",
                        }
                    ]
                }
            ],
        }
    ]
}


# Runtime tests script the MODEL as a pydantic-ai FunctionModel: the
# wire is the framework's to test, so we mock at the seams WE own (the
# model boundary via model_for, the search seam at httpx).
# A NON-canonical keyless source: the local-server posture.
_LOCAL_SOURCE = source("local", "http://o.test/v1")
_CANONICAL_SOURCE = source("openai", "https://api.openai.com/v1", api_key="k")
_TEST_SETTINGS = {
    "OPENAI_COMPATIBLE_SOURCES": _LOCAL_SOURCE,
    "SEARCH_PROVIDER": "dataforseo",
    "DATAFORSEO_LOGIN": "l",
    "DATAFORSEO_PASSWORD": "p",
}

_CONFIG = {
    "prompt": "Find the buyer at {{name}}",
    "provider": "openai_compatible",
    "source": "local",
    "model": "gemma4:12b",
    "tools": {"find_contacts": True},
    "outputs": [
        {"label": "Person", "type": "text", "description": "Full name"},
        {"label": "Profile", "type": "url", "description": "Their profile URL"},
    ],
}


class CatalogTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        openai_compatible.DOOR._roster_cache.clear()
        anthropic_compatible.DOOR._roster_cache.clear()
        openai_compatible.DOOR._probe_failed_at.clear()
        anthropic_compatible.DOOR._probe_failed_at.clear()

    def tearDown(self) -> None:
        openai_compatible.DOOR._roster_cache.clear()
        anthropic_compatible.DOOR._roster_cache.clear()
        openai_compatible.DOOR._probe_failed_at.clear()
        anthropic_compatible.DOOR._probe_failed_at.clear()

    def test_canonical_roster_is_chat_filtered_newest_first(self):
        def fake_get(url, **kwargs):
            # The canonical roster mixes chat and non-chat; only chat
            # families may reach the picker, newest first.
            return FakeResponse(
                200,
                {
                    "data": [
                        {"id": "gpt-5.2", "created": 1},
                        {"id": "text-embedding-3-small", "created": 3},
                        {"id": "gpt-6", "created": 2},
                        {"id": "gpt-4o-audio-preview", "created": 4},
                    ]
                },
            )

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(**{**_TEST_SETTINGS, "OPENAI_COMPATIBLE_SOURCES": _CANONICAL_SOURCE}),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        triples = [(m["provider"], m["source"], m["model"]) for m in body["models"]]
        self.assertNotIn(("openai_compatible", "openai", "text-embedding-3-small"), triples)
        self.assertNotIn(("openai_compatible", "openai", "gpt-4o-audio-preview"), triples)
        self.assertLess(
            triples.index(("openai_compatible", "openai", "gpt-6")),
            triples.index(("openai_compatible", "openai", "gpt-5.2")),
        )
        self.assertTrue(body["search_available"])

    def test_keyless_local_source_is_open_with_embed_hygiene(self):
        # A local Ollama through the spec door: keyless works because
        # the base is non-canonical, and embedding models are filtered
        # by the universal hygiene rule (the spec lists them unflagged).
        def fake_get(url, **kwargs):
            return FakeResponse(
                200,
                {"data": [{"id": "gemma4:12b", "created": 2}, {"id": "nomic-embed-text:latest", "created": 1}]},
            )

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        triples = [(m["provider"], m["source"], m["model"]) for m in body["models"]]
        self.assertIn(("openai_compatible", "local", "gemma4:12b"), triples)
        self.assertNotIn(("openai_compatible", "local", "nomic-embed-text:latest"), triples)

    def test_keyless_canonical_source_is_closed(self):
        with self.settings(OPENAI_COMPATIBLE_SOURCES=source("openai", "https://api.openai.com/v1")):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual([m for m in body["models"] if m["provider"] == "openai_compatible"], [])

    def test_two_sources_of_one_spec_coexist(self):
        # The whole point of named sources: a local server AND the
        # canonical vendor, side by side, grouped by source.
        def fake_get(url, **kwargs):
            if "o.test" in url:
                return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 1}]})
            return FakeResponse(200, {"data": [{"id": "gpt-5.2", "created": 1}]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES={**_LOCAL_SOURCE, **_CANONICAL_SOURCE}),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        triples = [(m["provider"], m["source"], m["model"]) for m in body["models"]]
        self.assertIn(("openai_compatible", "local", "gemma4:12b"), triples)
        self.assertIn(("openai_compatible", "openai", "gpt-5.2"), triples)

    def test_custom_base_takes_the_roster_as_served(self):
        # A self-hosted gateway's models must NOT be filtered by the
        # canonical vendor's name conventions.
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": "meta-llama/Llama-3.1-8B-Instruct", "created": 1}]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=source("vllm", "http://vllm.test/v1", api_key="k")),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        triples = [(m["provider"], m["source"], m["model"]) for m in body["models"]]
        self.assertIn(("openai_compatible", "vllm", "meta-llama/Llama-3.1-8B-Instruct"), triples)

    def test_canonical_probe_failure_is_an_honest_empty(self):
        # NO fallback floor (RULED, reversing the proto): the most
        # common failed canonical listing is a bad key, and a floor
        # would list models that can never run, hiding exactly the
        # misconfiguration the empty-picker guidance diagnoses.
        def fake_get(url, **kwargs):
            return FakeResponse(401, {})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(
                OPENAI_COMPATIBLE_SOURCES=_CANONICAL_SOURCE,
                ANTHROPIC_COMPATIBLE_SOURCES=source(
                    "anthropic", "https://api.anthropic.com", spec=ProviderSpec.ANTHROPIC_COMPATIBLE, api_key="k"
                ),
            ),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["models"], [])

    def test_a_successful_probe_caches_for_the_process(self):
        # One probe per source per process: later calls are dict hits
        # (a restart refreshes; only FAILED probes retry).
        calls = {"n": 0}

        def counting_get(url, **kwargs):
            calls["n"] += 1
            return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 1}]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=counting_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            self.client.get(reverse("agents_catalog"))
            self.client.get(reverse("agents_catalog"))
        self.assertEqual(calls["n"], 1)

    def test_catalog_truncation_rides_the_wire(self):
        # A silent cap would poison the vanished-model diagnosis for
        # addresses past the cap that still RUN.
        from agents.constants import CATALOG_MAX_MODELS

        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": f"m{i}", "created": i} for i in range(CATALOG_MAX_MODELS + 8)]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["models"]), CATALOG_MAX_MODELS)

    def test_the_catalog_carries_the_support_followup(self):
        from django.conf import settings as django_settings

        with (
            patch("agents.providers.base.httpx.get", side_effect=lambda url, **kw: FakeResponse(500, {})),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["support_followup"], django_settings.SUPPORT_FOLLOWUP)

    def test_an_oversize_model_name_never_reaches_the_picker(self):
        # Every address the catalog offers must be one Save accepts.
        def fake_get(url, **kwargs):
            return FakeResponse(
                200, {"data": [{"id": "gemma4:12b", "created": 2}, {"id": "x" * (MODEL_MAX_LENGTH + 1), "created": 1}]}
            )

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual([m["model"] for m in body["models"]], ["gemma4:12b"])

    def test_anthropic_custom_bases_filter_embedding_models(self):
        def fake_get(url, **kwargs):
            return FakeResponse(200, {"data": [{"id": "claude-x"}, {"id": "voyage-embed-2"}]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(
                OPENAI_COMPATIBLE_SOURCES={},
                ANTHROPIC_COMPATIBLE_SOURCES=source("gw", "http://gw.test", spec=ProviderSpec.ANTHROPIC_COMPATIBLE),
            ),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual([m["model"] for m in body["models"]], ["claude-x"])

    def test_a_failed_probe_heals_after_the_negative_ttl(self):
        # A boot-race timeout must not blank the catalog until restart,
        # but a dead source must not cost a serial probe timeout on
        # EVERY request either: within the TTL the failure answers from
        # memory; past it, the next request re-probes and heals.
        calls = {"n": 0}

        def flaky_get(url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("still booting")
            return FakeResponse(200, {"data": [{"id": "gemma4:12b", "created": 1}]})

        with (
            patch("agents.providers.base.httpx.get", side_effect=flaky_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=_LOCAL_SOURCE),
        ):
            first = self.client.get(reverse("agents_catalog")).json()
            within_ttl = self.client.get(reverse("agents_catalog")).json()
            probes_before_heal = calls["n"]
            # Age the remembered failure past the TTL in place (patching
            # time.monotonic would rewrite the SHARED time module).
            openai_compatible.DOOR._probe_failed_at["local"] -= PROBE_FAILURE_TTL_SECONDS + 1
            healed = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(first["models"], [])
        self.assertEqual(within_ttl["models"], [])
        self.assertEqual(probes_before_heal, 1, "within the TTL no re-probe fires")
        self.assertEqual(healed["models"][0]["model"], "gemma4:12b")

    def test_custom_base_probe_failure_has_no_floor(self):
        def fake_get(url, **kwargs):
            return FakeResponse(500, {})

        with (
            patch("agents.providers.base.httpx.get", side_effect=fake_get),
            self.settings(OPENAI_COMPATIBLE_SOURCES=source("vllm", "http://vllm.test/v1", api_key="k")),
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual([m for m in body["models"] if m["provider"] == "openai_compatible"], [])


def _inline_spawn(run_id: str, config: dict, row: dict, model=None) -> None:
    """The spawn seam, made synchronous: a REAL thread opens its own DB
    connection and cannot see the TestCase's uncommitted transaction."""
    from agents import views

    views._execute_test(run_id, config, row, model, close_connection=False)


class SearchAvailabilityTests(TestCase):
    """The default provider (dataforseo) needs CREDENTIALS to count as
    available: the tools gate off and searches skip honestly until the
    operator sets it up (force real setup over quietly degrading
    through a weak door)."""

    def setUp(self) -> None:
        login_session(self.client)

    def test_the_free_default_door_is_open_keyless_but_contacts_stay_gated(self):
        # DuckDuckGo is the default: web search works out of the box;
        # contact search still requires the DataForSEO setup.
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertTrue(body["search_available"])
        self.assertFalse(body["contacts_available"])

    def test_an_explicit_paid_door_without_credentials_is_unavailable(self):
        # No source override: an open source here would fire a REAL
        # roster probe (this test only concerns search availability).
        with self.settings(
            SEARCH_PROVIDER="dataforseo",
            DATAFORSEO_LOGIN="",
            DATAFORSEO_PASSWORD="",
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertFalse(body["search_available"])

    def test_duckduckgo_hits_map_and_failures_are_diagnosed(self):
        from agents.search import SearchHit, _DuckduckgoPage, search

        hit = SearchHit("Jane Doe | Site", "https://x.test/jane", "VP of Sales.")
        with (
            patch("agents.search._duckduckgo_fetch", return_value=_DuckduckgoPage(200, [hit])),
            self.settings(SEARCH_PROVIDER="duckduckgo"),
        ):
            outcome = search("acme")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.hits[0].url, "https://x.test/jane")

        # A status the door does not classify as a refusal is a
        # per-query error, reported once.
        with (
            patch("agents.search._duckduckgo_fetch", return_value=_DuckduckgoPage(500, [])),
            self.settings(SEARCH_PROVIDER="duckduckgo"),
        ):
            outcome = search("acme")
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.cause, "error")
        self.assertEqual(outcome.attempts, 1)

    def test_the_free_door_parses_the_engines_own_page_shape(self):
        # The library's parser is the one used (its xpaths are the
        # engine's page contract); this pins that the seam feeds it a
        # 200 and reads its results, with the status kept beside them.
        from agents.search import _duckduckgo_fetch

        class Page:
            status_code = 200
            text = (
                "<html><body><div class='result'><div class='body'><h2>Acme</h2>"
                "<a href='https://acme.com/'>Acme makes things.</a></div></div></body></html>"
            )

        with patch("ddgs.http_client2.HttpClient2.request", return_value=Page()):
            page = _duckduckgo_fetch("acme")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            [(h.title, h.url, h.snippet) for h in page.hits], [("Acme", "https://acme.com/", "Acme makes things.")]
        )

        class Challenge:
            status_code = 202
            text = "<html>challenge</html>"

        with patch("ddgs.http_client2.HttpClient2.request", return_value=Challenge()):
            page = _duckduckgo_fetch("acme")
        self.assertEqual((page.status_code, page.hits), (202, []))

    def test_credentialed_provider_is_available(self):
        with self.settings(**{**_TEST_SETTINGS, "OPENAI_COMPATIBLE_SOURCES": {}}):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertTrue(body["search_available"])
        self.assertTrue(body["contacts_available"])

    def test_contacts_pin_their_own_door_regardless_of_the_switch(self):
        # Web search on the free door + dataforseo credentials keeps
        # contacts fully available; the switch never gates them.
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="l", DATAFORSEO_PASSWORD="p"):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertTrue(body["search_available"])
        self.assertTrue(body["contacts_available"])

    def test_a_gated_tool_is_never_offered_to_the_model(self):
        from agents.runtime.tools import build_tools
        from openbower_schema.agents import AgentConfig

        config = AgentConfig(
            **{
                **_CONFIG,
                "tools": {"find_contacts": True, "web_search": True},
                "outputs": [{"key": "person", "label": "Person", "type": "text"}],
            }
        )
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""):
            offered = [t.name for t in build_tools(config)]
        self.assertEqual(offered, ["web_search"])

    def test_an_unusable_provider_reaching_the_seam_raises(self):
        # Availability gates keep the runtime away from here, so
        # arriving anyway is a CONFIG error, never a quiet skip.
        from agents.search import SearchMisconfigured, search

        with (
            self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""),
            self.assertRaises(SearchMisconfigured),
        ):
            search("anything")


def _serp_response(url, **kwargs):
    if "dataforseo" in url:
        return FakeResponse(200, _SERP)
    return FakeResponse(404, {})


def _raise_or_return(item):
    if isinstance(item, Exception):
        raise item
    return item


def _scripted_free_door(pages: list):
    """The free door scripted as the PAGES the engine answers with, in
    order (the last repeats): a `_DuckduckgoPage`, or an exception to
    raise. Returns the fetch stand-in and the list of queries it saw."""
    queue = list(pages)
    calls: list[str] = []

    def fetch(query):
        calls.append(query)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return fetch, calls


class _HeaderedResponse(FakeResponse):
    def __init__(self, status_code: int, json_data: dict | None = None, headers: dict | None = None) -> None:
        super().__init__(status_code, json_data)
        self.headers = headers or {}


class SearchBackoffTests(TestCase):
    """A rate limit is transport: the SAME query is retried on the
    schedule inside one seam call, the outcome says which door, why,
    and how many tries, and nothing else is retried at all."""

    def _free_door(self, pages: list) -> tuple[list[float], list[str]]:
        fetch, calls = _scripted_free_door(pages)
        sleeps: list[float] = []
        self.enterContext(patch("agents.search._duckduckgo_fetch", side_effect=fetch))
        self.enterContext(patch("agents.search._sleep", sleeps.append))
        self.enterContext(self.settings(SEARCH_PROVIDER="duckduckgo"))
        return sleeps, calls

    def test_a_challenged_free_door_retries_the_same_query_on_the_schedule(self):
        # 202 is the engine's bot challenge (a page with no results in
        # it), which the library would have read as "no results".
        from agents.search import SearchHit, _DuckduckgoPage, search

        hit = SearchHit("Acme", "https://acme.com", "Acme.")
        sleeps, calls = self._free_door(
            [_DuckduckgoPage(202, []), _DuckduckgoPage(429, []), _DuckduckgoPage(200, [hit])]
        )
        outcome = search("acme")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.provider, "duckduckgo")
        self.assertEqual(sleeps, list(SEARCH_BACKOFF_SECONDS[:2]))
        # The query itself is never rephrased.
        self.assertEqual(calls, ["acme"] * 3)

    def test_a_door_that_never_stops_refusing_exhausts_the_schedule(self):
        from agents.search import _DuckduckgoPage, search

        sleeps, _ = self._free_door([_DuckduckgoPage(202, [])])
        outcome = search("acme")
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.cause, "rate_limited")
        self.assertEqual(outcome.attempts, len(SEARCH_BACKOFF_SECONDS) + 1)
        self.assertEqual(sleeps, list(SEARCH_BACKOFF_SECONDS))

    def test_an_honest_empty_is_a_200_with_nothing_in_it(self):
        from agents.search import _DuckduckgoPage, search

        sleeps, calls = self._free_door([_DuckduckgoPage(200, [])])
        outcome = search("acme")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.hits, [])
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual((sleeps, calls), ([], ["acme"]))

    def test_a_free_door_timeout_is_reported_once(self):
        from agents.search import search

        sleeps, calls = self._free_door([DDGSTimeout("timed out")])
        outcome = search("acme")
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.cause, "timeout")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual((sleeps, calls), ([], ["acme"]))

    def _paid_door(self, responses: list) -> tuple[list[float], list[str]]:
        sleeps: list[float] = []
        keywords: list[str] = []
        queue = list(responses)

        def post(url, **kwargs):
            keywords.append(kwargs["json"][0]["keyword"])
            return queue.pop(0) if len(queue) > 1 else queue[0]

        self.enterContext(patch("agents.search.httpx.post", side_effect=post))
        self.enterContext(patch("agents.search._sleep", sleeps.append))
        self.enterContext(self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="l", DATAFORSEO_PASSWORD="p"))
        return sleeps, keywords

    def test_the_paid_door_honors_retry_after_clamped_to_the_schedule(self):
        from agents.search import search

        sleeps, keywords = self._paid_door(
            [
                _HeaderedResponse(429, {}, {"Retry-After": "3"}),
                _HeaderedResponse(429, {}, {"Retry-After": "600"}),
                FakeResponse(200, _SERP),
            ]
        )
        outcome = search("acme")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.provider, "dataforseo")
        self.assertEqual(sleeps, [3, max(SEARCH_BACKOFF_SECONDS)])
        self.assertEqual(keywords, ["acme"] * 3)

    def test_the_paid_doors_own_transient_task_code_is_a_rate_limit(self):
        from agents.search import DATAFORSEO_SE_ERROR, search

        sleeps, _ = self._paid_door(
            [
                FakeResponse(200, {"tasks": [{"status_code": DATAFORSEO_SE_ERROR, "status_message": "SE error"}]}),
                FakeResponse(200, _SERP),
            ]
        )
        outcome = search("acme")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(sleeps, [SEARCH_BACKOFF_SECONDS[0]])

    def test_a_paid_door_task_failure_is_an_error_reported_once(self):
        from agents.search import search

        sleeps, keywords = self._paid_door(
            [FakeResponse(200, {"tasks": [{"status_code": 40201, "status_message": "insufficient balance"}]})]
        )
        outcome = search("acme")
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.cause, "error")
        self.assertEqual(sleeps, [])
        self.assertEqual(len(keywords), 1)


def _tool_returned(messages) -> bool:
    return any(isinstance(part, ToolReturnPart) for m in messages for part in getattr(m, "parts", []))


def _final(info: AgentInfo, **values) -> ModelResponse:
    """A COMPLIANT answer: the schema requires every field now (that is
    what makes it strict), so a test scripting one output still sends
    the whole shape. Unscripted fields take the empty answer a model
    gives when it has nothing, which is a blank value at zero
    confidence."""
    args = dict(values)
    for name in info.output_tools[0].parameters_json_schema.get("properties", {}):
        args.setdefault(name, 0.0 if name.endswith(CONFIDENCE_SUFFIX) else "")
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


def _scripted_model(behavior) -> FunctionModel:
    """behavior(kind, messages, info) with kind agentic (tools offered)
    | answer (the tool-less call): one scripted model serves both."""

    def fn(messages, info: AgentInfo):
        return behavior("agentic" if info.function_tools else "answer", messages, info)

    return FunctionModel(fn)


def _default_behavior(answer_values: dict):
    """The happy script: search once, then answer the given values with
    a confidence that clears the floor (every kind states one; the
    runtime gates on nothing else)."""

    def behavior(kind, messages, info):
        if kind == "agentic" and not _tool_returned(messages):
            return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
        scored = dict(answer_values)
        for key, value in answer_values.items():
            if value:
                scored[f"{key}_bwr_confidence"] = 0.95
        return _final(info, **scored)

    return behavior


class RuntimeTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def _poll_result(self, resp) -> dict:
        """202 -> the run's terminal result (the spawn ran inline, so
        one poll is already terminal)."""
        self.assertEqual(resp.status_code, 202, resp.content)
        run = self.client.get(reverse("agents_test_run", args=[resp.json()["id"]])).json()
        self.assertEqual(run["status"], "complete", run)
        return run["result"]

    def _test_call(self, answer_values: dict, config: dict | None = None, serp=None, behavior=None):
        model = _scripted_model(behavior or _default_behavior(answer_values))
        with (
            # BOTH resolutions answer the scripted model: the view
            # resolves once and passes it down (run_cell only resolves
            # for direct callers).
            patch("agents.runtime.cell.model_for", return_value=model),
            patch("agents.views.model_for", return_value=model),
            patch("agents.search.httpx.post", side_effect=serp or _serp_response),
            patch("agents.views._spawn_test", new=_inline_spawn),
            self.settings(**_TEST_SETTINGS),
        ):
            resp = self.client.post(
                reverse("agents_test"),
                {"config": config or _CONFIG, "row": {"name": "Acme", "domain": "acme.com"}},
                content_type="application/json",
            )
        return self._poll_result(resp)

    def test_outputs_land_as_cells_with_evidence(self):
        body = self._test_call({"person": "Jane Doe", "profile": "https://www.linkedin.com/in/janedoe"})
        self.assertEqual(body["cells"]["person"], "Jane Doe")
        self.assertEqual(body["cells"]["profile"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(len(body["evidence"]), 1)

    def test_fabricated_url_is_blanked(self):
        body = self._test_call({"person": "Jane Doe", "profile": "https://made.up/fake-profile"})
        self.assertEqual(body["cells"]["person"], "Jane Doe")
        self.assertNotIn("profile", body["cells"])

    def test_mutated_url_grounds_to_the_evidence_form(self):
        # The model mutates the regional subdomain and adds a trailing
        # slash; the slug is real evidence, so the cell carries the
        # EVIDENCE'S url, never the model's rendering.
        body = self._test_call({"person": "Jane Doe", "profile": "https://au.linkedin.com/in/janedoe/"})
        self.assertEqual(body["cells"]["profile"], "https://www.linkedin.com/in/janedoe")

    def test_junk_output_args_validate_to_blanks(self):
        # The typed output is the schema: unknown keys drop, missing
        # keys default to "", and empty values write no cells.
        def behavior(kind, messages, info):
            if kind == "agentic" and not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "x"})])
            return _final(info, bogus="value", person="")

        body = self._test_call({}, behavior=behavior)
        self.assertEqual(body["cells"], {})

    def test_tools_on_but_no_evidence_writes_nothing(self):
        def dry_serp(url, **kwargs):
            if "dataforseo" in url:
                return FakeResponse(200, {"tasks": [{"status_code": 20000, "result": [{"items": []}]}]})
            return FakeResponse(404, {})

        body = self._test_call({"person": "Jane Doe", "profile": ""}, serp=dry_serp)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["evidence"], [])
        # The drought is DIAGNOSED: queries ran, zero hits, no failures
        # (the provider answered honestly; nothing matched).
        self.assertTrue(body["searches"])
        self.assertTrue(all(s["hits"] == 0 and not s["failed"] for s in body["searches"]))

    def test_provider_errors_are_diagnosed_as_failures(self):
        # A throttled/down provider must not read as a bad agent: the
        # cells stay blank, and the searches say WHY.
        def throttled(url, **kwargs):
            if "dataforseo" in url:
                return FakeResponse(429, {})
            return FakeResponse(404, {})

        body = self._test_call({"person": "Jane Doe", "profile": ""}, serp=throttled)
        self.assertEqual(body["cells"], {})
        self.assertTrue(body["searches"])
        self.assertTrue(all(s["failed"] for s in body["searches"]))

    def test_the_instruction_tail_switches_on_tool_presence(self):
        # One call site, two conducts: the tooled run is told to gather
        # evidence; the tool-less run is not told to use tools it lacks.
        seen: list[str] = []

        def behavior(kind, messages, info):
            seen.append(messages[0].instructions or "")
            if kind == "agentic" and not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "x"})])
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)

        self._test_call({"person": "Jane Doe", "profile": ""}, behavior=behavior)
        self.assertIn("Use the provided tools", seen[0])
        seen.clear()
        self._test_call({"person": "Jane Doe", "profile": ""}, config={**_CONFIG, "tools": {}}, behavior=behavior)
        self.assertTrue(seen and "Use the provided tools" not in seen[0])

    def test_non_string_row_values_reject(self):
        # The row is a DictField of CharFields: structured values are a
        # contract violation, not something to coerce quietly.
        with patch("agents.views.model_for"), patch("agents.views._spawn_test"), self.settings(**_TEST_SETTINGS):
            resp = self.client.post(
                reverse("agents_test"),
                {"config": _CONFIG, "row": {"name": {"nested": "no"}}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_row_keys_values_and_count_all_clamp(self):
        # Everything about the hand-fed row is authored input: key
        # length, value length, AND key count clamp (never reject; a
        # 17-variable prompt is a big prompt, not an error).
        long_key = "k" * (TEST_KEY_MAX_LENGTH + 8)
        with (
            patch("agents.views.model_for"),
            patch("agents.views._spawn_test") as spawn,
            self.settings(**_TEST_SETTINGS),
        ):
            resp = self.client.post(
                reverse("agents_test"),
                {"config": _CONFIG, "row": {long_key: "v" * (TEST_VALUE_MAX_LENGTH + 8)}},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 202)
            row = spawn.call_args.args[2]
            self.assertEqual(list(row), [long_key[:TEST_KEY_MAX_LENGTH]])
            self.assertEqual(len(row[long_key[:TEST_KEY_MAX_LENGTH]]), TEST_VALUE_MAX_LENGTH)

            # Clear the one-live-run guard; this test is about bounds.
            AgentTestRun.objects.all().delete()
            too_many = {f"k{i:02d}": "v" for i in range(TEST_ROW_MAX_KEYS + 8)}
            resp = self.client.post(
                reverse("agents_test"),
                {"config": _CONFIG, "row": too_many},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 202)
            row = spawn.call_args.args[2]
            self.assertEqual(len(row), TEST_ROW_MAX_KEYS)
            self.assertEqual(list(row), [f"k{i:02d}" for i in range(TEST_ROW_MAX_KEYS)])

    def test_a_failed_task_in_a_200_envelope_is_a_failure(self):
        # Insufficient balance answers 200 with a failed TASK; reading
        # it as an honest drought would hide exactly the check-your
        # -balance guidance the diagnosis system exists to fire.
        def broke(url, **kwargs):
            if "dataforseo" in url:
                return FakeResponse(200, {"tasks": [{"status_code": 40201, "status_message": "insufficient funds"}]})
            return FakeResponse(404, {})

        body = self._test_call({"person": "Jane Doe", "profile": ""}, serp=broke)
        self.assertEqual(body["cells"], {})
        self.assertTrue(body["searches"])
        self.assertTrue(all(s["failed"] for s in body["searches"]))

    def test_cells_carry_the_outputs_own_keys(self):
        # The runtime speaks config-local names; mapping onto a sheet's
        # row-data keys is the fill's concern (phase 5).
        config = {**_CONFIG, "outputs": [{"label": "Person", "type": "text", "description": "Full name"}]}
        body = self._test_call({"person": "Jane Doe"}, config=config)
        self.assertEqual(body["cells"], {"person": "Jane Doe"})


class AgenticLoopTests(TestCase):
    """The model-driven path: the model picks the queries, the budget
    bounds it, and a run that cannot answer writes nothing WITH its
    diagnosis (no fallback anywhere)."""

    def setUp(self) -> None:
        login_session(self.client)

    _TYPED_CONFIG = {
        **_CONFIG,
        "outputs": [
            {"key": "person", "label": "Person", "type": "text", "description": "Full name"},
            {"key": "profile", "label": "Profile", "type": "url", "description": "Their profile URL"},
        ],
    }

    def _run(
        self,
        behavior,
        serp=None,
        settings: dict | None = None,
        config: dict | None = None,
        row: dict | None = None,
    ) -> dict:
        from agents.runtime import run_cell
        from openbower_schema.agents import AgentConfig

        with (
            patch("agents.runtime.cell.model_for", return_value=_scripted_model(behavior)),
            patch("agents.search.httpx.post", side_effect=serp or _serp_response),
            self.settings(**(settings or _TEST_SETTINGS)),
        ):
            run = run_cell(AgentConfig(**(config or self._TYPED_CONFIG)), row or {"name": "Acme"})
        return {"cells": run.cells, "evidence": run.evidence, "searches": run.searches, "blank_cause": run.blank_cause}

    def test_model_drives_the_contacts_tool_with_the_scope_injected(self):
        # The model supplies query TERMS; the tool strips its site:
        # attempts, injects the people site, and pins dataforseo.
        serp_keywords: list[str] = []

        def serp(url, **kwargs):
            self.assertIn("dataforseo", url)
            serp_keywords.append(kwargs["json"][0]["keyword"])
            return FakeResponse(200, _SERP)

        def behavior(kind, messages, info):
            self.assertEqual(kind, "agentic")
            if _tool_returned(messages):
                return _final(
                    info,
                    person="Jane Doe",
                    profile="https://www.linkedin.com/in/janedoe",
                    person_bwr_confidence=0.95,
                    profile_bwr_confidence=0.95,
                )
            return ModelResponse(
                parts=[ToolCallPart(tool_name="find_contacts", args={"query": 'site:acme.com "VP Sales" Acme'})]
            )

        # DuckDuckGo is the configured door here, tripwired: ONLY the
        # contacts pin can route this query to dataforseo (under the
        # paid-door setting this test would pass with the pin deleted).
        ddg = patch(
            "agents.search._duckduckgo_fetch", side_effect=AssertionError("the free door must not serve contacts")
        )
        ddg.start()
        self.addCleanup(ddg.stop)
        body = self._run(behavior, serp=serp, settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"})
        self.assertEqual(len(serp_keywords), 1)
        self.assertTrue(serp_keywords[0].startswith("site:linkedin.com/in"))
        self.assertNotIn("site:acme.com", serp_keywords[0])
        self.assertEqual(body["cells"]["profile"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(len(body["searches"]), 1)
        self.assertEqual(len(body["evidence"]), 1)

    def test_an_uncited_answer_drops_and_diagnoses_unverified(self):
        # A search result is the best answer to the QUERY, not the
        # truth about the ROW: an answer that names no support is
        # unconfirmed, so the cell blanks and the cause says so.
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="Jane Doe", profile="")

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "unverified")

    def test_low_confidence_drops_and_diagnoses_unverified(self):
        # The model's stated confidence IS the gate: an answer below
        # the floor blanks and the cause says unverified (a guess and a
        # fact carry identical weight in a spreadsheet cell).
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.6)

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "unverified")

    def test_whether_a_record_fits_the_row_is_the_models_judgment(self):
        # The deliberate boundary, pinned so it stays deliberate. A
        # generic company name returns plausible strangers, and the
        # model answers with one at high confidence: the cell lands.
        # Deciding a Harbor Media record does not answer a Blue Thistle
        # question is judgment, and judgment is what an AI column is
        # bought for. The runtime once policed this by matching the
        # answer against company names, which read "Healthcare" as a
        # person and gave "B2B software" no check at all; a general
        # column cannot be built on that. The guard for this case is a
        # better model and the confidence it reports, not a string
        # rule here.
        def serp(url, **kwargs):
            return FakeResponse(
                200,
                {
                    "tasks": [
                        {
                            "status_code": 20000,
                            "result": [
                                {
                                    "items": [
                                        {
                                            "type": "organic",
                                            "title": "Rowan Vale - Co-Founder @ Harbor",
                                            "url": "https://uk.linkedin.com/in/rowanvale",
                                            "description": "Supported by Harbor Media's marketing engine.",
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                },
            )

        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "founder"})])
            return _final(
                info,
                person="Rowan Vale",
                profile="",
                person_bwr_confidence=0.95,
            )

        body = self._run(behavior, serp=serp, row={"name": "Blue Thistle Marketing"})
        self.assertEqual(body["cells"]["person"], "Rowan Vale")

    def test_an_answer_derived_from_the_task_itself_survives(self):
        # Classifications and row-fed echoes rest on the task's own
        # data, not on a record: nothing asks them to point at one.
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="B2B software", profile="", person_bwr_confidence=0.95)

        body = self._run(behavior)
        self.assertEqual(body["cells"]["person"], "B2B software")

    def _looping(self, serp_calls: list[str]):
        """A model that never stops searching: distinct queries per
        call (a repeated EXACT query dedupes without spending), so the
        loop actually spends the budget; and, when asked for a verdict
        with the tools withheld, an answer from the records."""
        calls = {"n": 0}

        def serp(url, **kwargs):
            serp_calls.append(url)
            return FakeResponse(200, _SERP)

        def behavior(kind, messages, info):
            if kind == "answer":
                self.assertIn("Records gathered", messages[0].parts[-1].content)
                return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)
            calls["n"] += 1
            return ModelResponse(
                parts=[ToolCallPart(tool_name="find_contacts", args={"query": f"VP Sales Acme {calls['n']}"})]
            )

        return behavior, serp

    def test_a_capped_run_answers_from_what_it_gathered(self):
        # RULED: the budget caps the SPEND, not the verdict. A model
        # still searching when the cap lands is asked once, tools
        # withheld, to judge the records it pooled; the confidence
        # floor and grounding judge that answer like any other.
        serp_calls: list[str] = []
        behavior, serp = self._looping(serp_calls)
        body = self._run(behavior, serp=serp)
        self.assertEqual(len(serp_calls), MAX_TOOL_CALLS)
        self.assertEqual(len(body["searches"]), MAX_TOOL_CALLS)
        self.assertEqual(body["cells"], {"person": "Jane Doe"})
        self.assertEqual(body["blank_cause"], "")

    def test_a_capped_run_with_nothing_gathered_is_no_answer(self):
        # Nothing pooled means nothing to judge from: no verdict call
        # is bought, and the cap is the settled outcome (never
        # model_error: refill must not re-buy the same refusal).
        serp_calls: list[str] = []
        behavior, _ = self._looping(serp_calls)

        def empty(url, **kwargs):
            serp_calls.append(url)
            return FakeResponse(200, {"tasks": [{"status_code": 20000, "result": [{"items": []}]}]})

        body = self._run(behavior, serp=empty)
        self.assertEqual(len(serp_calls), MAX_TOOL_CALLS)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "no_answer")

    def test_a_provider_5xx_stays_transient(self):
        # The infrastructure tier is untouched by the no_answer
        # tombstone: a 5xx (like a timeout) still parks the row for
        # retry rather than settling it.
        from pydantic_ai.exceptions import ModelHTTPError

        def behavior(kind, messages, info):
            raise ModelHTTPError(503, "gemma4:12b")

        body = self._run(behavior, config={**self._TYPED_CONFIG, "tools": {}})
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "transient")

    def test_an_sdk_timeout_is_transient_not_a_model_error(self):
        # The SDK catches httpx's timeout and re-raises ITS OWN, which
        # does not inherit from httpx.TimeoutException. Catching only
        # the transport's type therefore matched nothing a real door
        # can raise, and timeouts fell through to model_error: the
        # worker does not park those, so the row never retried, the
        # controller recorded a success and CLIMBED against a provider
        # already timing out, and the consecutive-transient breaker
        # never tripped. Raising the SDK type is the whole point of
        # this test; raising httpx's would pass against the old code.
        import anthropic
        import openai

        for timeout in (openai.APITimeoutError, anthropic.APITimeoutError):
            with self.subTest(exc=timeout.__name__):
                self.assertFalse(issubclass(timeout, httpx.TimeoutException))

                def behavior(kind, messages, info, exc=timeout):
                    raise exc(request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))

                body = self._run(behavior, config={**self._TYPED_CONFIG, "tools": {}})
                self.assertEqual(body["cells"], {})
                self.assertEqual(body["blank_cause"], "transient")

    def test_a_row_whose_every_search_failed_is_never_none_found(self):
        # A refusing door also drops connections, which the seam reads
        # as timeouts, and a single timeout never closes the door. A
        # run that ends with NO evidence and EVERY search failed was
        # never answered by the door, so it parks under the tool's
        # state instead of settling as no_evidence; a run with even one
        # answered search keeps its honest diagnosis.
        from agents.search import _DuckduckgoPage

        calls = {"n": 0}

        def behavior(kind, messages, info):
            calls["n"] += 1
            if calls["n"] <= 2:
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": f"Acme {calls['n']}"})])
            return _final(info, person="", profile="")

        with patch("agents.search._duckduckgo_fetch", side_effect=DDGSTimeout("timed out")):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual([s.cause for s in body["searches"]], ["timeout", "timeout"])
        self.assertEqual(body["blank_cause"], "search_throttled")

        calls["n"] = 0
        pages = iter([DDGSTimeout("timed out"), _DuckduckgoPage(200, [])])
        with patch("agents.search._duckduckgo_fetch", side_effect=lambda q: _raise_or_return(next(pages))):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual([s.cause for s in body["searches"]], ["timeout", ""])
        self.assertEqual(body["blank_cause"], "no_evidence")

    def test_a_closed_search_door_discards_the_answer_and_names_the_tool(self):
        # The model answers confidently from the residue of a throttled
        # run: the answer is discarded, the row's cause is the retry
        # state the TOOL owns, and the stored searches show the one
        # exhausted query.
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)

        def serp(url, **kwargs):
            return _HeaderedResponse(429, {}, {})

        with patch("agents.search._sleep"):
            body = self._run(behavior, serp=serp)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "contacts_throttled")
        self.assertEqual(len(body["searches"]), 1)
        self.assertEqual(body["searches"][0].cause, "rate_limited")

        def web_behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)

        from agents.search import _DuckduckgoPage

        challenged, _ = _scripted_free_door([_DuckduckgoPage(202, [])])
        with patch("agents.search._duckduckgo_fetch", side_effect=challenged), patch("agents.search._sleep"):
            body = self._run(
                web_behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["blank_cause"], "search_throttled")

    def test_a_gated_toggle_never_spends(self):
        # Tools toggled with every door closed is a DECIDABLE blank: no
        # completion is bought on the way to it.
        def behavior(kind, messages, info):
            raise AssertionError("the model must never be called")

        body = self._run(
            behavior,
            config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
            settings={"SEARCH_PROVIDER": "dataforseo", "DATAFORSEO_LOGIN": "", "DATAFORSEO_PASSWORD": ""},
        )
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["searches"], [])

    def test_a_url_embedded_in_prose_grounds_or_goes(self):
        # Prose is not a fabrication loophole: the fabricated URL is
        # excised from the text field; the mutated one rewrites to the
        # evidence's form in the url field.
        def behavior(kind, messages, info):
            if kind == "agentic" and not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(
                info,
                person="See https://made.up/fake for details",
                profile="https://au.linkedin.com/in/janedoe/",
                person_bwr_confidence=0.95,
                profile_bwr_confidence=0.95,
            )

        body = self._run(behavior)
        self.assertEqual(body["cells"]["person"], "See for details")
        self.assertEqual(body["cells"]["profile"], "https://www.linkedin.com/in/janedoe")

    def test_row_fed_urls_are_legitimate_for_toolless_agents(self):
        # The rendered prompt's own URLs join the allowed pool: a direct
        # agent can echo the row's website; fabrication still blanks.
        def behavior(kind, messages, info):
            return _final(
                info,
                person="https://made.up/fake",
                profile="https://acme.com/about",
                person_bwr_confidence=0.95,
                profile_bwr_confidence=0.95,
            )

        body = self._run(
            behavior,
            config={**self._TYPED_CONFIG, "prompt": "Normalize {{website}}", "tools": {}},
            row={"website": "https://acme.com/about"},
        )
        self.assertEqual(body["cells"], {"profile": "https://acme.com/about"})

    def test_an_auth_refusal_is_a_loud_config_error(self):
        # A revoked key fails every row identically: config tier, never
        # a quietly bad agent.
        from pydantic_ai.exceptions import ModelHTTPError

        from agents.providers import ModelUnavailable

        def behavior(kind, messages, info):
            raise ModelHTTPError(401, "gemma4:12b")

        with self.assertRaises(ModelUnavailable):
            self._run(behavior, config={**self._TYPED_CONFIG, "tools": {}})

    def test_a_toolless_config_answers_directly(self):
        # Tools are a parameter: an agent with none toggled answers
        # through the same single call, no searches, no evidence.
        def behavior(kind, messages, info):
            self.assertEqual(kind, "answer")
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)

        body = self._run(behavior, config={**self._TYPED_CONFIG, "tools": {}})
        self.assertEqual(body["cells"], {"person": "Jane Doe"})
        self.assertEqual(body["searches"], [])

    def test_a_failing_agentic_pass_writes_nothing(self):
        # A server rejecting the tools param surfaces as a raise inside
        # the run: blank cells, warning logged, no second code path
        # re-searching on a guess (RULED).
        def behavior(kind, messages, info):
            raise ValueError("server rejected the tools param")

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["searches"], [])

    def test_a_model_that_never_engages_writes_nothing(self):
        # A model answering from memory with tools on is the fabrication
        # path: blank cells, empty diagnosis, and the fix is a more
        # capable model (RULED), never a forced re-search.
        def behavior(kind, messages, info):
            return _final(info, person="From Memory", profile="")

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["searches"], [])

    def test_a_drought_is_never_researched(self):
        # The model DID search and honestly found nothing: exactly one
        # spend, diagnosed, blank cells; the deleted floor used to
        # re-search this case on a guess, double-spending the paid door.
        serp_calls: list[str] = []

        def serp(url, **kwargs):
            serp_calls.append(url)
            # WELL-FORMED empty: a bare {"tasks": []} envelope is a
            # provider FAILURE under the task-status guard, which
            # silently turned this into a failure test once.
            return FakeResponse(200, {"tasks": [{"status_code": 20000, "result": [{"items": []}]}]})

        def behavior(kind, messages, info):
            if kind == "agentic" and not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="Jane Doe", profile="")

        body = self._run(behavior, serp=serp)
        self.assertEqual(body["cells"], {})
        self.assertEqual(len(serp_calls), 1)
        self.assertEqual(len(body["searches"]), 1)
        self.assertFalse(body["searches"][0].failed, "an honest zero-hit answer is a drought, not an error")


class TestRunLifecycleTests(TestCase):
    """The poll contract itself: pending until the worker lands, failed
    on a crash (never a stuck pending), scoped 404s, stale-run purge."""

    def setUp(self) -> None:
        login_session(self.client)

    def _post(self):
        with self.settings(**_TEST_SETTINGS):
            return self.client.post(
                reverse("agents_test"),
                {"config": _CONFIG, "row": {"name": "Acme"}},
                content_type="application/json",
            )

    def test_post_answers_pending_before_the_worker_lands(self):
        with patch("agents.views._spawn_test") as spawn, patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 202, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "pending")
        self.assertIsNone(body["result"])
        spawn.assert_called_once()
        polled = self.client.get(reverse("agents_test_run", args=[body["id"]])).json()
        self.assertEqual(polled["status"], "pending")

    def test_a_crashing_run_lands_failed_not_stuck(self):
        with (
            patch("agents.views._spawn_test", new=_inline_spawn),
            patch("agents.views.model_for"),
            patch("agents.runtime.cell.model_for", side_effect=RuntimeError("boom")),
        ):
            resp = self._post()
        run = self.client.get(reverse("agents_test_run", args=[resp.json()["id"]])).json()
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["result"])
        # The wire carries the WHY: failure is the tier that needs its
        # diagnosis most.
        self.assertIn("crashed unexpectedly", run["error"])
        self.assertNotIn("boom", run["error"])
        # The profile-owned follow-up rides the crash leg intact
        # (bounded at boot, so the clamp cannot cut it mid-URL).
        from django.conf import settings as django_settings

        self.assertIn(django_settings.SUPPORT_FOLLOWUP, run["error"])

    def test_a_foreign_accounts_run_reads_as_missing(self):
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            run_id = self._post().json()["id"]
        AgentTestRun.objects.filter(id=run_id).update(account_id="01AC" + "Z" * 22)
        resp = self.client.get(reverse("agents_test_run", args=[run_id]))
        self.assertEqual(resp.status_code, 404)

    def test_an_unrunnable_address_refuses_at_post(self):
        # A config error refuses BEFORE a run row exists: it must never
        # masquerade as a started run.
        with self.settings(**_TEST_SETTINGS):
            resp = self.client.post(
                reverse("agents_test"),
                {"config": {**_CONFIG, "source": "ghost"}, "row": {"name": "Acme"}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("source unknown or closed", str(resp.json()["detail"]))
        self.assertEqual(AgentTestRun.objects.count(), 0)

    def test_truncated_row_keys_keep_the_first_value(self):
        stem = "k" * TEST_KEY_MAX_LENGTH
        with patch("agents.views.model_for"), patch("agents.views._spawn_test") as spawn:
            resp = self.client.post(
                reverse("agents_test"),
                {"config": _CONFIG, "row": {stem + "a": "first", stem + "b": "second"}},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        row = spawn.call_args.args[2]
        self.assertEqual(row, {stem: "first"})

    def test_the_debug_tag_refuses_at_validation(self):
        # {% debug %} dumps the context and sys.modules into the
        # rendered prompt: an information leak into a model call. The
        # guard walks the PARSED nodes, so the arg-carrying and nested
        # spellings refuse too (the tag ignores its arguments).
        for prompt in ("{% debug %}", "{% debug x %}", "{% if name %}{% debug %}{% endif %}"):
            resp = self.client.post(
                reverse("agents_test"),
                {"config": {**_CONFIG, "prompt": prompt}, "row": {}},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 400, prompt)

    def test_the_envelope_carries_the_worst_case_poll_budget(self):
        # The client's budget derives from the runtime's WORST CASE
        # (never invented client-side, and never the stale window: a
        # hung run must not spin the browser for the orphan margin).
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            body = self._post().json()
        self.assertEqual(body["poll_budget_seconds"], TEST_RUN_WORST_CASE_SECONDS)
        # The whole ordering, pinned: abandonment (poll silence) is
        # far below the worst case, which the stale window must clear
        # or an honest slow run is presented dead mid-flight.
        from agents.constants import TEST_RUN_ABANDON_SECONDS

        self.assertLess(TEST_RUN_ABANDON_SECONDS, TEST_RUN_WORST_CASE_SECONDS)
        self.assertGreater(TEST_RUN_STALE_PENDING_SECONDS, TEST_RUN_WORST_CASE_SECONDS)

    def test_your_own_run_blocks_young_and_supersedes_past_the_window(self):
        # YOUNG: its thread is still spending, so a second start 409s
        # (instant supersede would fork concurrent paid runs on every
        # reload-and-retest). PAST the abandonment window: the poll
        # loop is presumed gone, and the row is superseded so the
        # account is never locked out for the stale window.
        from agents.constants import TEST_RUN_ABANDON_SECONDS

        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            first = self._post()
            self.assertEqual(first.status_code, 202)
            young = self._post()
            self.assertEqual(young.status_code, 409)
            self.assertIn("still running", young.json()["detail"])
            aged = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_ABANDON_SECONDS + 1))
            AgentTestRun.objects.filter(id=first.json()["id"]).update(id=aged)
            second = self._post()
            self.assertEqual(second.status_code, 202)
        old = AgentTestRun.objects.get(id=aged)
        self.assertEqual(old.status, TestRunStatus.FAILED)
        self.assertIn("superseded", old.error)

    def test_the_fill_lanes_account_cap_gates_the_bench(self):
        # The bench is a metered lane like a fill: with the account's
        # fill slots full, a test refuses on the SAME constant the
        # fill lane reads, before any run row exists.
        from lists.constants import MAX_ACTIVE_FILLS
        from lists.models import Fill

        for _ in range(MAX_ACTIVE_FILLS):
            Fill.objects.create(
                account_id=TEST_IDENTITY["account_id"],
                user_id=TEST_IDENTITY["id"],
                list_id="01LIST" + "A" * 20,
                agent_id="01AGENT" + "A" * 19,
                column_keys=["answer"],
                config_snapshot={},
                confirmed_row_count=1,
            )
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], "fills_full")
        self.assertEqual(AgentTestRun.objects.count(), 0)

    def test_a_colleagues_live_run_refuses_and_is_never_superseded(self):
        # Foreign pendings win: no run id rides the 409 (adopting a
        # teammate's run would render their cells under your config),
        # and your abandoned row is NOT superseded past theirs (the
        # invariant is one live run per account, not one per user).
        theirs = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id="01OT" + "H" * 22)
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 409)
        self.assertNotIn("run_id", resp.json())
        self.assertIn("teammate", resp.json()["detail"])
        self.assertEqual(AgentTestRun.objects.get(id=str(theirs.id)).status, TestRunStatus.PENDING)

    def test_a_superseded_run_buys_no_work(self):
        # The tombstone gate covers the WORK: a run superseded while
        # its thread was scheduled must not spend a single completion
        # or search when its slot frees. A REAL config and a patched
        # run_cell: a gate deletion must fail this by running, never
        # pass via an unrelated crash on junk arguments.
        from agents.services import fail_run
        from agents.views import _execute_test
        from openbower_schema.agents import AgentConfig

        run = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        fail_run(str(run.id), "superseded by a newer test")
        with patch("agents.runtime.run_cell") as worker:
            keyed = {**_CONFIG, "outputs": [{"key": "", **o} for o in _CONFIG["outputs"]]}
            _execute_test(str(run.id), AgentConfig(**keyed), {"name": "Acme"}, close_connection=False)
        worker.assert_not_called()
        run.refresh_from_db()
        self.assertIn("superseded", run.error)

    def test_at_capacity_the_run_refuses_fast_never_queues(self):
        # Queue time is invisible to the published poll budget; a
        # queued run could present as interrupted having never begun.
        from agents import views

        run = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        drained = views.threading.BoundedSemaphore(1)
        drained.acquire()
        with patch.object(views, "_TEST_SLOTS", drained):
            views._execute_test(str(run.id), None, {}, close_connection=False)
        run.refresh_from_db()
        self.assertEqual(run.status, TestRunStatus.FAILED)
        self.assertIn("capacity", run.error)

    def test_a_foreign_pending_older_than_the_worst_case_never_blocks(self):
        # Past the worst case a pending row provably cannot be live;
        # blocking on it was ~15 minutes of dead lockout.
        theirs = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id="01OT" + "H" * 22)
        aged = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_WORST_CASE_SECONDS + 1))
        AgentTestRun.objects.filter(id=str(theirs.id)).update(id=aged)
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 202)

    def test_a_polled_then_silent_run_is_superseded(self):
        # The signal branch that matters most: a run that WAS polled
        # and then went quiet past the window (closed tab) supersedes.
        from agents.constants import TEST_RUN_ABANDON_SECONDS

        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            first = self._post()
            AgentTestRun.objects.filter(id=first.json()["id"]).update(
                polled_at=timezone.now() - timedelta(seconds=TEST_RUN_ABANDON_SECONDS + 1)
            )
            second = self._post()
        self.assertEqual(second.status_code, 202)
        old = AgentTestRun.objects.get(id=first.json()["id"])
        self.assertEqual(old.status, TestRunStatus.FAILED)
        self.assertIn("superseded", old.error)

    def test_an_abandoned_teammates_run_stops_blocking(self):
        # Abandonment is judged the SAME for foreign rows: a dead
        # orphan must not lock the whole account for the worst case.
        from agents.constants import TEST_RUN_ABANDON_SECONDS

        theirs = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id="01OT" + "H" * 22)
        AgentTestRun.objects.filter(id=str(theirs.id)).update(
            polled_at=timezone.now() - timedelta(seconds=TEST_RUN_ABANDON_SECONDS + 1)
        )
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(AgentTestRun.objects.get(id=str(theirs.id)).status, TestRunStatus.FAILED)

    def test_a_recently_polled_old_run_is_not_abandoned(self):
        # Abandonment is observed SILENCE, never age: an honest slow
        # run past the old 32s age window still has a live poll loop
        # stamping polled_at every cadence.
        from agents.constants import TEST_RUN_ABANDON_SECONDS

        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            first = self._post()
            aged = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_ABANDON_SECONDS + 8))
            AgentTestRun.objects.filter(id=first.json()["id"]).update(id=aged, polled_at=timezone.now())
            second = self._post()
        self.assertEqual(second.status_code, 409)
        self.assertIn("still running", second.json()["detail"])

    def test_the_poll_get_stamps_the_abandonment_signal(self):
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            run_id = self._post().json()["id"]
        self.assertIsNone(AgentTestRun.objects.get(id=run_id).polled_at)
        self.client.get(reverse("agents_test_run", args=[run_id]))
        self.assertIsNotNone(AgentTestRun.objects.get(id=run_id).polled_at)

    def test_a_teammates_poll_never_stamps_liveness(self):
        # OWNER-scoped: a colleague reading a run by hand must not
        # keep an orphan whose owner's loop is gone reading as live.
        theirs = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id="01OT" + "H" * 22)
        self.client.get(reverse("agents_test_run", args=[str(theirs.id)]))
        self.assertIsNone(AgentTestRun.objects.get(id=str(theirs.id)).polled_at)

    def test_the_409_carries_its_machine_code(self):
        AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id="01OT" + "H" * 22)
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            resp = self._post()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "test_run_active")

    def test_terminal_writes_never_overwrite_a_tombstone(self):
        # A superseded run's zombie thread finishing later must not
        # flip the row back to complete with results from a config the
        # user already replaced.
        from agents.services import complete_run, fail_run

        run = AgentTestRun.objects.create(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        fail_run(str(run.id), "superseded by a newer test")
        complete_run(str(run.id), {"cells": {}, "evidence": [], "searches": []})
        run.refresh_from_db()
        self.assertEqual(run.status, TestRunStatus.FAILED)
        self.assertIn("superseded", run.error)

    def test_an_orphaned_pending_run_polls_as_failed(self):
        # Daemon threads die unwound on restarts; the poll leg presents
        # the orphan as its failure instead of pending forever.
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            run_id = self._post().json()["id"]
        # Aging rewrites the id: BOTH legs (guard and poll) judge the
        # ULID's time prefix, one fact, never two columns.
        stale_id = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_STALE_PENDING_SECONDS + 1))
        AgentTestRun.objects.filter(id=run_id).update(id=stale_id)
        run = self.client.get(reverse("agents_test_run", args=[stale_id])).json()
        self.assertEqual(run["status"], "failed")
        self.assertIn("interrupted", run["error"])

    def test_unknown_run_is_404(self):
        resp = self.client.get(reverse("agents_test_run", args=["01" + "Z" * 24]))
        self.assertEqual(resp.status_code, 404)

    def test_stale_runs_purge_on_post_and_only_stale_ones(self):
        with patch("agents.views._spawn_test"), patch("agents.views.model_for"):
            from openbower_kernel.fields import min_ulid_at

            # Aging rewrites ids (see the spend-guard test): the purge
            # rides the id index via the ULID time prefix.
            stale_id = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_MAX_AGE_SECONDS + 1))
            AgentTestRun.objects.filter(id=self._post().json()["id"]).update(id=stale_id)
            boundary_id = min_ulid_at(timezone.now() - timedelta(seconds=TEST_RUN_MAX_AGE_SECONDS - 1))
            AgentTestRun.objects.filter(id=self._post().json()["id"]).update(id=boundary_id)
            fresh_id = self._post().json()["id"]
        # Inside the window survives (a purge that swept everything
        # would pass a weaker assertion).
        self.assertFalse(AgentTestRun.objects.filter(id=stale_id).exists())
        self.assertTrue(AgentTestRun.objects.filter(id=boundary_id).exists())
        self.assertTrue(AgentTestRun.objects.filter(id=fresh_id).exists())

"""The catalog's honesty (only runnable models) and the runtime's
grounding doctrine. Mocking sits at the seams WE own: search and roster
probes at httpx, the model as a scripted FunctionModel through
model_for (the framework's wire is not ours to test).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
from ddgs.exceptions import TimeoutException as DDGSTimeout
from django.test import TestCase
from django.urls import reverse
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.constants import (
    MAX_TOOL_CALLS,
    MODEL_MAX_LENGTH,
    PROBE_FAILURE_TTL_SECONDS,
    SEARCH_BACKOFF_SECONDS,
)
from agents.providers import anthropic_compatible, openai_compatible
from common.testing import FakeResponse, login_session
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
        openai_compatible.PROVIDER._roster_cache.clear()
        anthropic_compatible.PROVIDER._roster_cache.clear()
        openai_compatible.PROVIDER._probe_failed_at.clear()
        anthropic_compatible.PROVIDER._probe_failed_at.clear()

    def tearDown(self) -> None:
        openai_compatible.PROVIDER._roster_cache.clear()
        anthropic_compatible.PROVIDER._roster_cache.clear()
        openai_compatible.PROVIDER._probe_failed_at.clear()
        anthropic_compatible.PROVIDER._probe_failed_at.clear()

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
        self.assertEqual(body["tools"]["web_search"], "open")

    def test_keyless_local_source_is_open_with_embed_hygiene(self):
        # A local Ollama through the spec provider: keyless works because
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
            openai_compatible.PROVIDER._probe_failed_at["local"] -= PROBE_FAILURE_TTL_SECONDS + 1
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


class SearchAvailabilityTests(TestCase):
    """The default provider (dataforseo) needs CREDENTIALS to count as
    available: the tools gate off and searches skip honestly until the
    operator sets it up (force real setup over quietly degrading
    through a weak provider)."""

    def setUp(self) -> None:
        login_session(self.client)

    def test_the_free_default_provider_is_open_keyless_but_contacts_stay_gated(self):
        # DuckDuckGo is the default: web search works out of the box;
        # contact search still requires the DataForSEO setup.
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["tools"], {"web_search": "open", "find_contacts": "not_configured"})

    def test_an_explicit_paid_provider_without_credentials_is_unavailable(self):
        # No source override: an open source here would fire a REAL
        # roster probe (this test only concerns search availability).
        with self.settings(
            SEARCH_PROVIDER="dataforseo",
            DATAFORSEO_LOGIN="",
            DATAFORSEO_PASSWORD="",
        ):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["tools"]["web_search"], "not_configured")

    def test_duckduckgo_hits_map_and_failures_are_diagnosed(self):
        from agents.tools.search.providers.base import SearchHit
        from agents.tools.search.providers.duckduckgo import _Page
        from agents.tools.search.providers.schedule import search

        hit = SearchHit("Jane Doe | Site", "https://x.test/jane", "VP of Sales.")
        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", return_value=_Page(200, [hit])),
            self.settings(SEARCH_PROVIDER="duckduckgo"),
        ):
            answer = search("acme", provider="duckduckgo")
        self.assertEqual(answer.hits[0].url, "https://x.test/jane")

        # A status the provider does not classify as a refusal is a
        # per-query error, RAISED once (typed, with the audit facts).
        from agents.tools.search.errors import SearchErrored

        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", return_value=_Page(500, [])),
            self.settings(SEARCH_PROVIDER="duckduckgo"),
            self.assertRaises(SearchErrored) as caught,
        ):
            search("acme", provider="duckduckgo")
        self.assertEqual(caught.exception.attempts, 1)

    def test_the_free_provider_parses_the_engines_own_page_shape(self):
        # The library's parser is the one used (its xpaths are the
        # engine's page contract); this pins that the seam feeds it a
        # 200 and reads its results, with the status kept beside them.
        from agents.tools.search.providers.duckduckgo import _fetch

        class Page:
            status_code = 200
            text = (
                "<html><body><div class='result'><div class='body'><h2>Acme</h2>"
                "<a href='https://acme.com/'>Acme makes things.</a></div></div></body></html>"
            )

        with patch("ddgs.http_client2.HttpClient2.request", return_value=Page()):
            page = _fetch("acme")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            [(h.title, h.url, h.snippet) for h in page.hits], [("Acme", "https://acme.com/", "Acme makes things.")]
        )

        class Challenge:
            status_code = 202
            text = "<html>challenge</html>"

        with patch("ddgs.http_client2.HttpClient2.request", return_value=Challenge()):
            page = _fetch("acme")
        self.assertEqual((page.status_code, page.hits), (202, []))

    def test_credentialed_provider_is_available(self):
        with self.settings(**{**_TEST_SETTINGS, "OPENAI_COMPATIBLE_SOURCES": {}}):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["tools"], {"web_search": "open", "find_contacts": "open"})

    def test_contacts_pin_their_own_provider_regardless_of_the_switch(self):
        # Web search on the free provider + dataforseo credentials keeps
        # contacts fully available; the switch never gates them.
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="l", DATAFORSEO_PASSWORD="p"):
            body = self.client.get(reverse("agents_catalog")).json()
        self.assertEqual(body["tools"], {"web_search": "open", "find_contacts": "open"})

    def test_a_gated_tool_is_never_offered_to_the_model(self):
        from agents.constants import SearchStatus
        from agents.runtime.deps import CellDeps
        from agents.tools import registry as tool_registry
        from openbower_schema.agents import AgentConfig

        config = AgentConfig(
            **{
                **_CONFIG,
                "tools": {"find_contacts": True, "web_search": True},
                "outputs": [{"key": "person", "label": "Person", "type": "text"}],
            }
        )
        deps = CellDeps()
        with self.settings(SEARCH_PROVIDER="duckduckgo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""):
            for tool in tool_registry.all_tools():
                deps.tool_status[tool.name] = tool.availability()
            offered = [t.name for t in tool_registry.build_tools(config, deps)]
        self.assertEqual(offered, ["web_search"])
        self.assertEqual(deps.tool_status["find_contacts"], SearchStatus.NOT_CONFIGURED)

    def test_an_unconfigured_provider_reaching_the_seam_raises_not_configured(self):
        # The runtime's gates keep an unconfigured provider from being
        # asked; a call that arrives anyway raises the TYPED refusal
        # with zero attempts, never making a live call.
        from agents.tools.search.errors import SearchNotConfigured
        from agents.tools.search.providers.schedule import search

        with (
            self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""),
            self.assertRaises(SearchNotConfigured) as caught,
        ):
            search("anything", provider="dataforseo")
        self.assertEqual(caught.exception.attempts, 0)


def _serp_response(url, **kwargs):
    if "dataforseo" in url:
        return FakeResponse(200, _SERP)
    return FakeResponse(404, {})


def _raise_or_return(item):
    if isinstance(item, Exception):
        raise item
    return item


def _scripted_free_provider(pages: list):
    """The free provider scripted as the PAGES the engine answers with, in
    order (the last repeats): a `_Page`, or an exception to
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
    """A rate limit is the harness's: the SAME query is retried on the
    schedule inside one call, the outcome says which provider, why,
    and how many tries, and nothing else is retried at all."""

    def _free_provider(self, pages: list) -> tuple[list[float], list[str]]:
        fetch, calls = _scripted_free_provider(pages)
        sleeps: list[float] = []
        self.enterContext(patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=fetch))
        self.enterContext(patch("agents.tools.search.providers.schedule._sleep", sleeps.append))
        self.enterContext(self.settings(SEARCH_PROVIDER="duckduckgo"))
        return sleeps, calls

    def test_a_challenged_free_provider_retries_the_same_query_on_the_schedule(self):
        # 202 is the engine's bot challenge (a page with no results in
        # it), which the library would have read as "no results".
        from agents.tools.search.providers.base import SearchHit
        from agents.tools.search.providers.duckduckgo import _Page
        from agents.tools.search.providers.schedule import search

        hit = SearchHit("Acme", "https://acme.com", "Acme.")
        sleeps, calls = self._free_provider([_Page(202, []), _Page(429, []), _Page(200, [hit])])
        outcome = search("acme", provider="duckduckgo")
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.provider, "duckduckgo")
        self.assertEqual(sleeps, list(SEARCH_BACKOFF_SECONDS[:2]))
        # The query itself is never rephrased.
        self.assertEqual(calls, ["acme"] * 3)

    def test_a_provider_that_never_stops_refusing_exhausts_the_schedule(self):
        from agents.tools.search.errors import SearchRateLimited
        from agents.tools.search.providers.duckduckgo import _Page
        from agents.tools.search.providers.schedule import search

        sleeps, _ = self._free_provider([_Page(202, [])])
        with self.assertRaises(SearchRateLimited) as caught:
            search("acme", provider="duckduckgo")
        self.assertEqual(caught.exception.attempts, len(SEARCH_BACKOFF_SECONDS) + 1)
        self.assertEqual(sleeps, list(SEARCH_BACKOFF_SECONDS))

    def test_an_honest_empty_is_a_200_with_nothing_in_it(self):
        from agents.tools.search.providers.duckduckgo import _Page
        from agents.tools.search.providers.schedule import search

        sleeps, calls = self._free_provider([_Page(200, [])])
        outcome = search("acme", provider="duckduckgo")
        self.assertEqual(outcome.hits, [])
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual((sleeps, calls), ([], ["acme"]))

    def test_an_unreachable_free_provider_is_reported_once(self):
        # A timeout and a dropped connection both read as unreachable
        # (the library wraps both in its own exceptions), once, no
        # retry: the next query may get through. The second fixture is
        # the library's BASE exception, so this fails if the seam
        # narrows back to catching timeouts alone.
        from ddgs.exceptions import DDGSException

        from agents.tools.search.errors import SearchUnreachable
        from agents.tools.search.providers.schedule import search

        for exc in (DDGSTimeout("timed out"), DDGSException("Server disconnected")):
            with self.subTest(exc=type(exc).__name__):
                sleeps, calls = self._free_provider([exc])
                with self.assertRaises(SearchUnreachable) as caught:
                    search("acme", provider="duckduckgo")
                self.assertEqual(caught.exception.attempts, 1)
                self.assertEqual((sleeps, calls), ([], ["acme"]))

    def _paid_provider(self, responses: list) -> tuple[list[float], list[str]]:
        sleeps: list[float] = []
        keywords: list[str] = []
        queue = list(responses)

        def post(url, **kwargs):
            keywords.append(kwargs["json"][0]["keyword"])
            return queue.pop(0) if len(queue) > 1 else queue[0]

        self.enterContext(patch("agents.tools.search.providers.dataforseo.httpx.post", side_effect=post))
        self.enterContext(patch("agents.tools.search.providers.schedule._sleep", sleeps.append))
        self.enterContext(self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="l", DATAFORSEO_PASSWORD="p"))
        return sleeps, keywords

    def test_the_paid_provider_honors_retry_after_clamped_to_the_schedule(self):
        from agents.tools.search.providers.schedule import search

        sleeps, keywords = self._paid_provider(
            [
                _HeaderedResponse(429, {}, {"Retry-After": "3"}),
                _HeaderedResponse(429, {}, {"Retry-After": "600"}),
                FakeResponse(200, _SERP),
            ]
        )
        outcome = search("acme", provider="dataforseo")
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.provider, "dataforseo")
        self.assertEqual(sleeps, [3, max(SEARCH_BACKOFF_SECONDS)])
        self.assertEqual(keywords, ["acme"] * 3)

    def test_a_hostile_retry_after_takes_the_schedule(self):
        # A negative, NaN, or non-numeric delay is a broken header,
        # not a schedule: NaN poisons min() and a negative wait raises
        # out of sleep as a settled model error. Each falls back to
        # the schedule's step; FAILS if _retry_after stops rejecting
        # them.
        from agents.tools.search.providers.schedule import search

        for value in ("-5", "nan", "inf", "soon"):
            with self.subTest(value=value):
                sleeps, _ = self._paid_provider(
                    [_HeaderedResponse(429, {}, {"Retry-After": value}), FakeResponse(200, _SERP)]
                )
                outcome = search("acme", provider="dataforseo")
                self.assertEqual(outcome.attempts, 2)
                self.assertEqual(sleeps, [SEARCH_BACKOFF_SECONDS[0]])

    def test_the_paid_providers_own_transient_task_code_is_a_rate_limit(self):
        from agents.tools.search.providers.dataforseo import SE_ERROR
        from agents.tools.search.providers.schedule import search

        sleeps, _ = self._paid_provider(
            [
                FakeResponse(200, {"tasks": [{"status_code": SE_ERROR, "status_message": "SE error"}]}),
                FakeResponse(200, _SERP),
            ]
        )
        outcome = search("acme", provider="dataforseo")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(sleeps, [SEARCH_BACKOFF_SECONDS[0]])

    def test_a_paid_provider_task_failure_is_an_error_reported_once(self):
        from agents.tools.search.errors import SearchErrored
        from agents.tools.search.providers.schedule import search

        sleeps, keywords = self._paid_provider(
            [FakeResponse(200, {"tasks": [{"status_code": 40201, "status_message": "insufficient balance"}]})]
        )
        with self.assertRaises(SearchErrored):
            search("acme", provider="dataforseo")
        self.assertEqual(sleeps, [])
        self.assertEqual(len(keywords), 1)

    def test_a_paid_provider_transport_failure_is_unreachable(self):
        from agents.tools.search.providers.schedule import search

        def post(url, **kwargs):
            raise httpx.ConnectError("connection refused")

        self.enterContext(patch("agents.tools.search.providers.dataforseo.httpx.post", side_effect=post))
        from agents.tools.search.errors import SearchUnreachable

        self.enterContext(self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="l", DATAFORSEO_PASSWORD="p"))
        with self.assertRaises(SearchUnreachable) as caught:
            search("acme", provider="dataforseo")
        self.assertEqual(caught.exception.attempts, 1)


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

    def _test_call(self, answer_values: dict, config: dict | None = None, serp=None, behavior=None):
        """One run, straight through run_cell, projected into the
        WIRE shape the worker writes (CellRunResult): the runtime
        behaviors these tests pin are lane-independent, and the test
        lane's own endpoint contract lives with the fills tests."""
        from agents.serializers import AgentConfigRequest
        from lists.services.cell_run import run_cell
        from openbower_schema.agents import AgentConfig
        from openbower_schema.fills import CellRunResult

        serializer = AgentConfigRequest(data=config or _CONFIG)
        serializer.is_valid(raise_exception=True)
        model = _scripted_model(behavior or _default_behavior(answer_values))
        with (
            patch("agents.runtime.answer.answerer.model_for", return_value=model),
            patch("agents.tools.search.providers.dataforseo.httpx.post", side_effect=serp or _serp_response),
            self.settings(**_TEST_SETTINGS),
        ):
            run = run_cell(AgentConfig(**serializer.validated_data), {"name": "Acme", "domain": "acme.com"})
        return CellRunResult(
            cells=dict(run.cells),
            evidence=list(run.evidence),
            tool_calls=[o.wire() for o in run.tool_calls],
            assessments=dict(run.assessments),
            declined_cause=run.declined_cause,
            blamed_tool=run.blamed_tool,
            tools=dict(run.tools),
        ).model_dump()

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
        self.assertTrue(body["tool_calls"])
        self.assertTrue(all(s["hits"] == 0 and s["status"] == "open" for s in body["tool_calls"]))

    def test_provider_errors_are_diagnosed_as_failures(self):
        # A throttled/down provider must not read as a bad agent: the
        # cells stay blank, and the searches say WHY.
        def throttled(url, **kwargs):
            if "dataforseo" in url:
                return _HeaderedResponse(429, {}, {})
            return FakeResponse(404, {})

        with patch("agents.tools.search.providers.schedule._sleep"):
            body = self._test_call({"person": "Jane Doe", "profile": ""}, serp=throttled)
        self.assertEqual(body["cells"], {})
        self.assertTrue(body["tool_calls"])
        self.assertTrue(all(s["status"] == "rate_limited" for s in body["tool_calls"]))
        # The run names the tools that did not serve: without this
        # map a blank cell loses its degraded mark (the run-detail
        # read serves it back to the bench verbatim). FAILS if
        # run_cell stops recording per-tool statuses; the wire
        # projection itself is pinned where the worker writes it.
        self.assertEqual(body["tools"], {"find_contacts": "rate_limited"})

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
        self.assertTrue(body["tool_calls"])
        self.assertTrue(all(s["status"] == "error" for s in body["tool_calls"]))

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
        from lists.services.cell_run import run_cell
        from openbower_schema.agents import AgentConfig

        with (
            patch("agents.runtime.answer.answerer.model_for", return_value=_scripted_model(behavior)),
            patch("agents.tools.search.providers.dataforseo.httpx.post", side_effect=serp or _serp_response),
            self.settings(**(settings or _TEST_SETTINGS)),
        ):
            run = run_cell(AgentConfig(**(config or self._TYPED_CONFIG)), row or {"name": "Acme"})
        return {
            "cells": run.cells,
            "evidence": run.evidence,
            "tool_calls": run.tool_calls,
            "declined_cause": run.declined_cause,
            "tools": run.tools,
        }

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

        # DuckDuckGo is the configured provider here, tripwired: ONLY the
        # contacts pin can route this query to dataforseo (under the
        # paid-provider setting this test would pass with the pin deleted).
        ddg = patch(
            "agents.tools.search.providers.duckduckgo._fetch",
            side_effect=AssertionError("the free provider must not serve contacts"),
        )
        ddg.start()
        self.addCleanup(ddg.stop)
        body = self._run(behavior, serp=serp, settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"})
        self.assertEqual(len(serp_keywords), 1)
        self.assertTrue(serp_keywords[0].startswith("site:linkedin.com/in"))
        self.assertNotIn("site:acme.com", serp_keywords[0])
        self.assertEqual(body["cells"]["profile"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(len(body["tool_calls"]), 1)
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
        self.assertEqual(body["declined_cause"], "unverified")

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
        self.assertEqual(body["declined_cause"], "unverified")

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
        call, so the loop actually spends the budget; and, when asked
        for a verdict with the tools withheld, an answer from the
        records."""
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
        self.assertEqual(len(body["tool_calls"]), MAX_TOOL_CALLS)
        self.assertEqual(body["cells"], {"person": "Jane Doe"})

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
        self.assertEqual(body["declined_cause"], "no_answer")

    def test_a_provider_5xx_stays_transient(self):
        # The infrastructure tier is untouched by the no_answer
        # tombstone: a 5xx (like a timeout) still parks the row for
        # retry rather than settling it.
        from pydantic_ai.exceptions import ModelHTTPError

        def behavior(kind, messages, info):
            raise ModelHTTPError(503, "gemma4:12b")

        body = self._run(behavior, config={**self._TYPED_CONFIG, "tools": {}})
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["declined_cause"], "transient")

    def test_an_sdk_timeout_is_transient_not_a_model_error(self):
        # The SDK catches httpx's timeout and re-raises ITS OWN, which
        # does not inherit from httpx.TimeoutException. Catching only
        # the transport's type therefore matched nothing a real provider
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
                self.assertEqual(body["declined_cause"], "transient")

    def test_a_wrapped_transport_failure_is_transient_not_a_model_error(self):
        # The framework wraps the SDKs' WHOLE connection family
        # (timeouts included, which subclass the SDK connection error)
        # as ModelAPIError, so the bare-SDK arm alone matches nothing a
        # wrapped provider path raises. A refused connection to a local
        # model must park the row, never settle it: FAILS if the
        # ModelAPIError arm is removed and the failure falls to the
        # fatal blanket.
        import openai
        from pydantic_ai.exceptions import ModelAPIError

        def refused(kind, messages, info):
            try:
                raise openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434/v1"))
            except openai.APIConnectionError as cause:
                raise ModelAPIError(model_name="m", message="connection error") from cause

        body = self._run(refused, config={**self._TYPED_CONFIG, "tools": {}})
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["declined_cause"], "transient")

    def test_a_wrapped_sdk_timeout_still_reads_as_a_timeout(self):
        # The cause classifies: a wrapped APITimeoutError takes the
        # timeout leg, everything else in the family reads unreachable;
        # both are transient either way.
        import openai
        from pydantic_ai.exceptions import ModelAPIError

        def timed_out(kind, messages, info):
            try:
                raise openai.APITimeoutError(request=httpx.Request("POST", "http://localhost:11434/v1"))
            except openai.APITimeoutError as cause:
                raise ModelAPIError(model_name="m", message="timed out") from cause

        body = self._run(timed_out, config={**self._TYPED_CONFIG, "tools": {}})
        self.assertEqual(body["declined_cause"], "transient")

    def test_a_provider_that_only_ever_failed_settles_as_unavailable_not_none_found(self):
        # A refusing provider also drops connections, which the seam reads
        # as unreachable, and a single one never closes the provider
        # mid-run. A provider that was asked, never answered, and
        # contributed nothing takes its last status at the end of the
        # run: the row parks under tool_unavailable with the status on
        # the task, never "none found". A provider that answered even once
        # (an honest empty) keeps the honest diagnosis.
        from agents.tools.search.providers.duckduckgo import _Page

        calls = {"n": 0}

        def behavior(kind, messages, info):
            calls["n"] += 1
            if calls["n"] <= 2:
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": f"Acme {calls['n']}"})])
            return _final(info, person="", profile="")

        with patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=DDGSTimeout("timed out")):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual([s.status for s in body["tool_calls"]], ["unreachable", "unreachable"])
        self.assertEqual(body["declined_cause"], "tool_unavailable")
        self.assertEqual(body["tools"], {"web_search": "unreachable"})

        calls["n"] = 0
        pages = iter([DDGSTimeout("timed out"), _Page(200, [])])
        with patch(
            "agents.tools.search.providers.duckduckgo._fetch", side_effect=lambda q: _raise_or_return(next(pages))
        ):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual([s.status for s in body["tool_calls"]], ["unreachable", "open"])
        self.assertEqual(body["declined_cause"], "no_evidence")
        self.assertEqual(body["tools"], {"web_search": "open"})

    def test_a_closed_provider_with_nothing_gathered_parks_the_row_by_base_code(self):
        # The model answers confidently from the residue of a throttled
        # run that pooled NOTHING: there is no evidence to ground on,
        # the row parks under tool_unavailable, and the task records
        # which tool and what its provider said.
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(info, person="Jane Doe", profile="", person_bwr_confidence=0.95)

        def serp(url, **kwargs):
            return _HeaderedResponse(429, {}, {})

        with patch("agents.tools.search.providers.schedule._sleep"):
            body = self._run(behavior, serp=serp)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["declined_cause"], "tool_unavailable")
        self.assertEqual(body["tools"], {"find_contacts": "rate_limited"})
        self.assertEqual(len(body["tool_calls"]), 1)
        self.assertEqual(body["tool_calls"][0].status, "rate_limited")

    def test_a_degraded_tool_never_discards_an_answer_the_other_tool_grounded(self):
        # web_search is rate limited; find_contacts answers; the model
        # grounds a confident answer on the contacts record. The cell
        # FILLS, and the run's tool statuses say web_search was
        # degraded: the user sees both the value and the mark.
        from agents.tools.search.providers.duckduckgo import _Page

        def behavior(kind, messages, info):
            returned = [part for m in messages for part in getattr(m, "parts", []) if isinstance(part, ToolReturnPart)]
            if not returned:
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            if len(returned) == 1:
                return ModelResponse(parts=[ToolCallPart(tool_name="find_contacts", args={"query": "VP Sales Acme"})])
            return _final(
                info,
                person="Jane Doe",
                profile="https://www.linkedin.com/in/janedoe",
                person_bwr_confidence=0.95,
                profile_bwr_confidence=0.95,
            )

        challenged, _ = _scripted_free_provider([_Page(202, [])])
        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=challenged),
            patch("agents.tools.search.providers.schedule._sleep"),
        ):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True, "find_contacts": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual(body["cells"]["person"], "Jane Doe")
        # The DECLINED cause still names the degraded provider: a column
        # this run left unanswered lands as tool_unavailable at once
        # (no park; the filled siblings would be held hostage), and a
        # later Continue re-targets it.
        self.assertEqual(body["declined_cause"], "tool_unavailable")
        self.assertEqual(body["tools"], {"web_search": "rate_limited", "find_contacts": "open"})
        self.assertEqual([s.tool for s in body["tool_calls"]], ["web_search", "find_contacts"])

    def test_two_closed_providers_name_the_first_in_the_enum(self):
        # Blank cell, both providers closed with DIFFERENT cell states:
        # web_search unreachable (tool_unavailable), find_contacts not
        # configured (tool_not_configured), so the assertion pins
        # WHICH tool names the cell, not merely that one did. The
        # order is registration order; FAILS if the blame walk
        # walks it reversed. Contacts is never offered (seeded
        # closed), so the model sees one tool and declines after it
        # fails.
        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            return _final(info, person="", profile="")

        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=DDGSTimeout("timed out")),
            patch("agents.tools.search.providers.schedule._sleep"),
        ):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True, "find_contacts": True}},
                settings={
                    **_TEST_SETTINGS,
                    "SEARCH_PROVIDER": "duckduckgo",
                    "DATAFORSEO_LOGIN": "",
                    "DATAFORSEO_PASSWORD": "",
                },
            )
        self.assertEqual(body["declined_cause"], "tool_unavailable")
        self.assertEqual(body["tools"], {"web_search": "unreachable", "find_contacts": "not_configured"})

    def test_a_not_configured_tool_is_the_cell_state_without_a_spend(self):
        # Tools toggled with every provider not configured: no completion
        # is bought, and the cell says which tool is not set up.
        def behavior(kind, messages, info):
            raise AssertionError("the model must never be called")

        body = self._run(
            behavior,
            config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
            settings={"SEARCH_PROVIDER": "dataforseo", "DATAFORSEO_LOGIN": "", "DATAFORSEO_PASSWORD": ""},
        )
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["tool_calls"], [])
        self.assertEqual(body["declined_cause"], "tool_not_configured")
        self.assertEqual(body["tools"], {"web_search": "not_configured"})

    def test_a_provider_that_served_cannot_name_the_blank(self):
        # web_search serves a real record, a later query rate-limits
        # (the closer overwrites the provider's status; `served` remembers),
        # and the model reads its evidence and declines every output.
        # The decline is the model's verdict on evidence it HAD, so the
        # blank settles NO_EVIDENCE; blaming the provider would park the
        # row to re-buy the same verdict four times. The degradation
        # still rides the tools map. FAILS without the served skip in
        # the naming (the old read was tool_unavailable, a retry).
        from agents.tools.search.providers.base import SearchHit
        from agents.tools.search.providers.duckduckgo import _Page

        def behavior(kind, messages, info):
            returned = [part for m in messages for part in getattr(m, "parts", []) if isinstance(part, ToolReturnPart)]
            if len(returned) < 2:
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": f"q{len(returned)}"})])
            return _final(info, person="", profile="")

        hit = SearchHit("Acme", "https://acme.com", "Acme.")
        fetch, _ = _scripted_free_provider([_Page(200, [hit]), _Page(403, [])])
        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=fetch),
            patch("agents.tools.search.providers.schedule._sleep"),
        ):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["declined_cause"], "no_evidence")
        self.assertEqual(body["tools"], {"web_search": "rate_limited"})
        self.assertEqual([s.status for s in body["tool_calls"]], ["open", "rate_limited"])

    def test_an_unconfigured_sibling_still_names_a_declined_output(self):
        # DELIBERATE: web_search serves and the model declines, but a
        # toggled find_contacts was never offered (its provider is not
        # configured), and the missing tool may be exactly why the
        # output is empty. The cell reads tool_not_configured, which
        # re-runs on Continue once the provider is set up; provider credentials
        # live in deployment settings, outside the config fingerprint,
        # so no settled state could re-open on setup. The price is a
        # consent-gated re-buy per Continue until then.
        from agents.tools.search.providers.base import SearchHit
        from agents.tools.search.providers.duckduckgo import _Page

        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            return _final(info, person="", profile="")

        hit = SearchHit("Acme", "https://acme.com", "Acme.")
        fetch, _ = _scripted_free_provider([_Page(200, [hit])])
        with patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=fetch):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True, "find_contacts": True}},
                settings={
                    **_TEST_SETTINGS,
                    "SEARCH_PROVIDER": "duckduckgo",
                    "DATAFORSEO_LOGIN": "",
                    "DATAFORSEO_PASSWORD": "",
                },
            )
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["declined_cause"], "tool_not_configured")
        self.assertEqual(body["tools"], {"web_search": "open", "find_contacts": "not_configured"})

    def test_a_model_transient_outranks_a_closed_provider(self):
        # The RANK, pinned: the answerer's own cause wins over the
        # doctrine's. A completion that 5xxs while the provider is
        # rate-limited parks as TRANSIENT, the more specific fact; the
        # accepted edge of the same rank is that a TERMINAL answerer
        # cause (no_answer, unparseable) also settles a run whose provider
        # closed. FAILS if the two verdict legs swap.
        from pydantic_ai.exceptions import ModelHTTPError

        from agents.tools.search.providers.duckduckgo import _Page

        def behavior(kind, messages, info):
            if not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            raise ModelHTTPError(status_code=503, model_name="scripted", body=None)

        fetch, _ = _scripted_free_provider([_Page(403, [])])
        with (
            patch("agents.tools.search.providers.duckduckgo._fetch", side_effect=fetch),
            patch("agents.tools.search.providers.schedule._sleep"),
        ):
            body = self._run(
                behavior,
                config={**self._TYPED_CONFIG, "tools": {"web_search": True}},
                settings={**_TEST_SETTINGS, "SEARCH_PROVIDER": "duckduckgo"},
            )
        self.assertEqual(body["declined_cause"], "transient")
        self.assertEqual(body["tools"], {"web_search": "rate_limited"})

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
        self.assertEqual(body["tool_calls"], [])

    def test_a_failing_agentic_pass_writes_nothing(self):
        # A server rejecting the tools param surfaces as a raise inside
        # the run: blank cells, warning logged, no second code path
        # re-searching on a guess (RULED).
        def behavior(kind, messages, info):
            raise ValueError("server rejected the tools param")

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["tool_calls"], [])

    def test_a_model_that_never_engages_writes_nothing(self):
        # A model answering from memory with tools on is the fabrication
        # path: blank cells, empty diagnosis, and the fix is a more
        # capable model (RULED), never a forced re-search.
        def behavior(kind, messages, info):
            return _final(info, person="From Memory", profile="")

        body = self._run(behavior)
        self.assertEqual(body["cells"], {})
        self.assertEqual(body["tool_calls"], [])

    def test_a_drought_is_never_researched(self):
        # The model DID search and honestly found nothing: exactly one
        # spend, diagnosed, blank cells; the deleted floor used to
        # re-search this case on a guess, double-spending the paid provider.
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
        self.assertEqual(len(body["tool_calls"]), 1)
        self.assertFalse(body["tool_calls"][0].failed, "an honest zero-hit answer is a drought, not an error")

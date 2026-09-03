"""The runtime's end-to-end behaviors: one scripted model and mocked
providers through run_cell, asserting the doctrine outcomes (grounded
cells, no-spend refusals, the named blanks, the surviving record).
Born as the v1/v2 golden-parity suite; the flip kept the behaviors as
the runtime's own spec.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart

from agents.runtime.cell import run_cell
from lists.constants import StoredCellState
from openbower_schema.agents import AgentConfig

from .test_catalog_and_runtime import (
    _final,
    _HeaderedResponse,
    _scripted_model,
    _serp_response,
    _tool_returned,
)

_PAID_PROVIDER = {"SEARCH_PROVIDER": "dataforseo", "DATAFORSEO_LOGIN": "l", "DATAFORSEO_PASSWORD": "p"}


def _config(**tools) -> AgentConfig:
    return AgentConfig(
        prompt="Find the buyer at {{name}}",
        provider="openai_compatible",
        source="local",
        model="m",
        tools=tools,
        outputs=[
            {"key": "person", "label": "Person", "type": "text", "description": "Full name"},
            {"key": "profile", "label": "Profile", "type": "url", "description": "Their profile URL"},
        ],
    )


def _search_then_answer(**values):
    """Search once through web_search, then answer the given values at
    a floor-clearing confidence."""

    def behavior(kind, messages, info):
        if kind == "agentic" and not _tool_returned(messages):
            return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
        scored = dict(values)
        for key, value in values.items():
            if value:
                scored.setdefault(f"{key}_bwr_confidence", 0.95)
        return _final(info, **scored)

    return behavior


class RuntimeBehaviorTests(SimpleTestCase):
    def _run(self, config, behavior, serp=None):
        # The answerer resolves the model from the config alone, so
        # the script rides a model_for patch at the seam the suite
        # already owns.
        with (
            patch("agents.tools.search.providers.dataforseo.httpx.post", side_effect=serp or _serp_response),
            patch("agents.tools.search.providers.schedule._sleep"),
            self.settings(**_PAID_PROVIDER),
            patch("agents.runtime.answer.answerer.model_for", return_value=_scripted_model(behavior)),
        ):
            return run_cell(config, {"name": "Acme"})

    def test_direct_answer(self):
        def behavior(kind, messages, info):
            return _final(
                info, person="Jane Doe", person_bwr_confidence=0.95, person_bwr_confidence_reason="well known"
            )

        run = self._run(_config(), behavior)
        self.assertEqual(run.cells, {"person": "Jane Doe"})
        self.assertEqual(run.declined_cause, StoredCellState.NO_EVIDENCE)

    def test_tooled_run_grounds_and_reports_open_tools(self):
        config = _config(web_search=True, find_contacts=True)
        run = self._run(config, _search_then_answer(person="Jane Doe", profile="https://www.linkedin.com/in/janedoe"))
        self.assertEqual(run.cells["profile"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(len(run.evidence), 1)
        self.assertEqual(run.tools, {"web_search": "open", "find_contacts": "open"})

    def test_unconfigured_providers_blank_without_spend(self):
        def behavior(kind, messages, info):  # pragma: no cover - the guard must fire first
            raise AssertionError("no completion may be bought with no tool available")

        config = _config(web_search=True, find_contacts=True)
        with (
            self.settings(SEARCH_PROVIDER="dataforseo", DATAFORSEO_LOGIN="", DATAFORSEO_PASSWORD=""),
            patch("agents.runtime.answer.answerer.model_for", return_value=_scripted_model(behavior)),
        ):
            run = run_cell(config, {"name": "Acme"})
        self.assertEqual(run.cells, {})
        self.assertEqual(run.declined_cause, StoredCellState.TOOL_NOT_CONFIGURED)
        self.assertEqual(run.tools, {"web_search": "not_configured", "find_contacts": "not_configured"})

    def test_a_rate_limited_provider_parks_not_blames_the_agent(self):
        def throttled(url, **kwargs):
            return _HeaderedResponse(429, {}, {})

        run = self._run(_config(web_search=True, find_contacts=True), _search_then_answer(), serp=throttled)
        self.assertEqual(run.cells, {})
        self.assertEqual(run.declined_cause, StoredCellState.TOOL_UNAVAILABLE)
        self.assertEqual(run.tools["web_search"], "rate_limited")
        self.assertEqual([o.wire().status for o in run.tool_calls], ["rate_limited"])

    def test_model_throttle_after_search_keeps_the_record(self):
        # The CALL fails after a search already served: the diagnostics
        # (evidence, searches, tools) must survive the failure, because
        # a parked row's stored run is the audit. In v2 the facts ride
        # the AgentError raise; this is the pin that they arrive.
        def behavior(kind, messages, info):
            if kind == "agentic" and not _tool_returned(messages):
                return ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={"query": "Acme"})])
            raise ModelHTTPError(status_code=429, model_name="m", body=None)

        run = self._run(_config(web_search=True), behavior)
        self.assertEqual(run.cells, {})
        self.assertEqual(run.declined_cause, StoredCellState.TRANSIENT)
        self.assertEqual(len(run.evidence), 1)
        self.assertEqual(run.tools, {"web_search": "open"})

    def test_a_confidence_floor_drop_reads_unverified(self):
        config = _config(web_search=True)
        run = self._run(config, _search_then_answer(person="Maybe Someone", person_bwr_confidence=0.5))
        self.assertEqual(run.cells, {})
        self.assertEqual(run.declined_cause, StoredCellState.UNVERIFIED)
        self.assertEqual(run.assessments["person"]["dropped"], "Maybe Someone")

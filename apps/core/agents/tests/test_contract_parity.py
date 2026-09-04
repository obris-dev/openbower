"""The wire contract's AgentProvider Literal and the registry roster
must name the same values, or valid agents fail zod client-side.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from openbower_schema.agents import AgentProvider as WireAgentProvider
from openbower_schema.agents import AgentTools


class WireProviderParityTests(SimpleTestCase):
    def test_provider_parity(self):
        # Registry names, not an enum: the roster registered at
        # ready() (which the test runner's setup already ran) is the
        # server truth the wire Literal must mirror.
        from agents.providers.registry import provider_names

        self.assertEqual(set(get_args(WireAgentProvider)), set(provider_names()))


class ToolPropertyParityTests(SimpleTestCase):
    def test_config_tool_properties_match_the_enum_keys(self):
        # The contract model's named tool reads hardcode wire strings;
        # this pins them to the Django enum so neither side drifts.
        from openbower_schema.agents import AgentConfig

        config = AgentConfig(
            prompt="p",
            provider="openai_compatible",
            source="s",
            model="m",
            tools=dict.fromkeys(AgentTools.model_fields, True),
            outputs=[{"key": "value", "label": "Value", "type": "text"}],
        )
        self.assertTrue(config.finds_contacts)
        self.assertTrue(config.searches_web)
        self.assertTrue(config.uses_tools)
        bare = config.model_copy(update={"tools": AgentTools()})
        self.assertFalse(bare.uses_tools)
        # ANY single toggle counts: uses_tools derives over every
        # field, so a tool the named properties never mention still
        # arms the no-spend, fabrication, and budget guards.
        for field in AgentTools.model_fields:
            with self.subTest(only=field):
                one = config.model_copy(update={"tools": AgentTools(**{field: True})})
                self.assertTrue(one.uses_tools)


class DuplicatedKnowledgePins(SimpleTestCase):
    """Facts declared in two homes that cannot import each other: each
    pair pins here so drift fails a test instead of shipping."""

    def test_settings_serp_provider_mirror_matches_the_enum(self):
        from agents.constants import SearchProvider
        from agents.tools.search.providers.registry import all_providers
        from conf.settings import base as settings_base
        from openbower_schema.agents import SearchProviderWire

        self.assertEqual({p.name for p in all_providers()}, {p.value for p in SearchProvider})
        self.assertEqual(set(settings_base._SEARCH_PROVIDER_CHOICES), {p.value for p in SearchProvider})
        self.assertEqual(set(get_args(SearchProviderWire)), {p.value for p in SearchProvider})

    def test_the_search_familys_codes_are_search_statuses_with_phrases(self):
        # ask_provider coerces SearchStatus(failure.code) while
        # recording the audit, and the copy builder looks the phrase
        # up directly: a new family error class whose code misses
        # either home would raise INSIDE a fill (dressed as a tool
        # crash), so both memberships pin here instead.
        from agents.constants import SearchStatus
        from agents.tools.search.errors import SEARCH_ERRORS
        from agents.tools.search.machinery import STATUS_PHRASE

        for error in SEARCH_ERRORS:
            with self.subTest(code=error.code):
                SearchStatus(error.code)
                self.assertIn(error.code, STATUS_PHRASE)

    def test_the_worst_case_counts_the_verdict_and_the_clamped_backoff(self):
        # The two terms once missing from the derivation, pinned
        # independently: the capped verdict is a SECOND run with its
        # own request budget, and a provider's Retry-After stretches every
        # wait to the schedule's LARGEST step (the clamp honors the
        # ask up to max(schedule), not up to that attempt's own step).
        # FAILS if either term falls back out of the formula, which
        # the grace test alone cannot catch (it compares compose to
        # the constant, not the constant to its parts).
        from agents.constants import (
            CELL_RUN_WORST_CASE_SECONDS,
            COMPLETION_TIMEOUT_SECONDS,
            DATAFORSEO_TIMEOUT_SECONDS,
            MODEL_RETRIES,
            SEARCH_BACKOFF_SECONDS,
        )
        from openbower_schema.agents import MAX_TOOL_CALLS

        floor = (MAX_TOOL_CALLS + 3 + 1 + MODEL_RETRIES) * COMPLETION_TIMEOUT_SECONDS + MAX_TOOL_CALLS * (
            DATAFORSEO_TIMEOUT_SECONDS + len(SEARCH_BACKOFF_SECONDS) * max(SEARCH_BACKOFF_SECONDS)
        )
        self.assertGreaterEqual(CELL_RUN_WORST_CASE_SECONDS, floor)

    def test_the_cron_sweeps_test_fills(self):
        # The test-fill TTL is a compose cron, never an admission
        # preflight (a bench click must not pay a sweep, and an idle
        # deploy still purges). Two files carry it: the compose cron
        # service runs supercronic over the crontab, and the crontab's
        # line is the schedule. FAILS if either half is dropped.
        from pathlib import Path

        root = Path(__file__).resolve().parents[4]
        compose = (root / "docker-compose.yml").read_text()
        self.assertIn("cron:", compose)
        self.assertIn("supercronic /app/apps/core/crontab", compose)
        crontab = (root / "apps" / "core" / "crontab").read_text()
        self.assertIn("manage.py sweep_test_fills", crontab)

    def test_the_worker_topology_and_graces_hold(self):
        # Compose cannot import the constant, so the FILL worker's
        # stop_grace_period restates the worst case by hand (a grace
        # below it SIGKILLs a legitimately slow row through its outcome
        # write). Grace is attributed PER SERVICE, never max()d: the
        # test worker's deliberately short grace must not satisfy the
        # fill worker's bound. The kinds flags are pinned here too,
        # because the two-instance topology's whole isolation is the
        # claim filter each command line carries.
        import re
        from pathlib import Path

        from agents.constants import CELL_RUN_WORST_CASE_SECONDS

        compose = (Path(__file__).resolve().parents[4] / "docker-compose.yml").read_text()
        services: dict[str, dict[str, str]] = {}
        current = ""
        for line in compose.splitlines():
            top = re.match(r"^  (\w[\w-]*):\s*$", line)
            if top:
                current = top.group(1)
                continue
            grace = re.match(r"^\s*stop_grace_period:\s*(\d+)s\s*$", line)
            if grace and current:
                services.setdefault(current, {})["grace"] = grace.group(1)
            command = re.match(r"^\s*command:.*fill_worker(.*)$", line)
            if command and current:
                services.setdefault(current, {})["kinds"] = command.group(1).strip()
        graced = {name: conf for name, conf in services.items() if "grace" in conf}
        self.assertEqual(set(graced), {"worker", "worker-test"})
        self.assertGreater(int(graced["worker"]["grace"]), CELL_RUN_WORST_CASE_SECONDS)
        self.assertEqual(graced["worker"]["kinds"], "--kinds normal")
        self.assertEqual(graced["worker-test"]["kinds"], "--kinds test")


class ReservedKeyParityPins(SimpleTestCase):
    def test_the_shipped_reserved_set_matches_the_runtime_predicate(self):
        # The predicate lives beside create_model (runtime/answer.py);
        # the WEB's readiness mirror reads the contract document's
        # x-reserved-output-keys. Drift between them means a name the
        # checklist passes and the server refuses (or vice versa).
        import json
        from pathlib import Path

        from pydantic import BaseModel

        from agents.runtime.answer import reserved_output_key

        doc = json.loads((Path(__file__).resolve().parents[4] / "packages/openbower-schema/schema.json").read_text())
        shipped = set(doc["x-reserved-output-keys"])
        computed = {name for name in dir(BaseModel) if not name.startswith("_")}
        self.assertEqual(shipped, computed)
        for name in shipped:
            self.assertTrue(reserved_output_key(name), name)

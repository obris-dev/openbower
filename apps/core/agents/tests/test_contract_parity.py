"""The wire contract's AgentProvider Literal and the Django enum must
name the same values, or valid agents fail zod client-side.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from agents.constants import AgentProvider, AgentTool
from openbower_schema.agents import AgentProvider as WireAgentProvider
from openbower_schema.agents import AgentTools


class WireEnumParityTests(SimpleTestCase):
    def test_provider_parity(self):
        self.assertEqual(set(get_args(WireAgentProvider)), {p.value for p in AgentProvider})


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
            tools={tool.value: True for tool in AgentTool},
            outputs=[{"key": "value", "label": "Value", "type": "text"}],
        )
        self.assertTrue(config.finds_contacts)
        self.assertTrue(config.searches_web)
        self.assertTrue(config.uses_tools)
        bare = config.model_copy(update={"tools": AgentTools()})
        self.assertFalse(bare.uses_tools)

    def test_tools_submodel_fields_match_the_enum(self):
        # The wire's CLOSED tool shape and the Django enum are two
        # homes for one key set; the web derives from the wire.
        self.assertEqual(set(AgentTools.model_fields), {tool.value for tool in AgentTool})


class DuplicatedKnowledgePins(SimpleTestCase):
    """Facts declared in two homes that cannot import each other: each
    pair pins here so drift fails a test instead of shipping."""

    def test_test_run_status_wire_matches_enum(self):
        from agents.constants import TestRunStatus
        from openbower_schema.agents import TestRunStatus as WireStatus

        self.assertEqual(set(get_args(WireStatus)), {s.value for s in TestRunStatus})

    def test_settings_serp_door_mirror_matches_the_enum(self):
        from agents.constants import SearchProvider
        from agents.search import _DOORS
        from conf.settings import base as settings_base
        from openbower_schema.agents import SearchProviderWire

        self.assertEqual(set(_DOORS), set(SearchProvider))
        self.assertEqual(set(settings_base._SEARCH_DOORS), {p.value for p in SearchProvider})
        self.assertEqual(set(get_args(SearchProviderWire)), {p.value for p in SearchProvider})

    def test_the_worst_case_counts_the_verdict_and_the_clamped_backoff(self):
        # The two terms once missing from the derivation, pinned
        # independently: the capped verdict is a SECOND run with its
        # own request budget, and a door's Retry-After stretches every
        # wait to the schedule's LARGEST step (the clamp honors the
        # ask up to max(schedule), not up to that attempt's own step).
        # FAILS if either term falls back out of the formula, which
        # the grace test alone cannot catch (it compares compose to
        # the constant, not the constant to its parts).
        from agents.constants import (
            COMPLETION_TIMEOUT_SECONDS,
            DATAFORSEO_TIMEOUT_SECONDS,
            MODEL_RETRIES,
            SEARCH_BACKOFF_SECONDS,
            TEST_RUN_WORST_CASE_SECONDS,
        )
        from openbower_schema.agents import MAX_TOOL_CALLS

        floor = (MAX_TOOL_CALLS + 3 + 1 + MODEL_RETRIES) * COMPLETION_TIMEOUT_SECONDS + MAX_TOOL_CALLS * (
            DATAFORSEO_TIMEOUT_SECONDS + len(SEARCH_BACKOFF_SECONDS) * max(SEARCH_BACKOFF_SECONDS)
        )
        self.assertGreaterEqual(TEST_RUN_WORST_CASE_SECONDS, floor)

    def test_the_worker_stop_grace_clears_one_runs_worst_case(self):
        # Compose cannot import the constant, so the worker's
        # stop_grace_period restates it by hand: a grace BELOW the
        # worst case SIGKILLs a legitimately slow row through its
        # outcome write, which is exactly what the grace exists to
        # prevent. The worst case moves whenever a timeout, the tool
        # budget, or the search backoff schedule moves; this is what
        # makes the compose value follow.
        import re
        from pathlib import Path

        from agents.constants import TEST_RUN_WORST_CASE_SECONDS

        compose = (Path(__file__).resolve().parents[4] / "docker-compose.yml").read_text()
        graces = [int(value) for value in re.findall(r"^\s*stop_grace_period:\s*(\d+)s\s*$", compose, re.MULTILINE)]
        self.assertEqual(len(graces), 1, "one worker grace expected in docker-compose.yml")
        self.assertGreater(graces[0], TEST_RUN_WORST_CASE_SECONDS)


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


class ComposedMessageBoundsPins(SimpleTestCase):
    def test_the_crash_message_never_meets_the_error_clamp(self):
        # The follow-up is operator config bounded at boot; this holds
        # the arithmetic so the composed message cannot truncate
        # mid-URL through fail_run's clamp.
        from django.conf import settings

        from agents.constants import TEST_RUN_ERROR_MAX_LENGTH
        from agents.views import CRASH_MESSAGE_PREFIX

        self.assertLessEqual(
            len(CRASH_MESSAGE_PREFIX) + settings.SUPPORT_FOLLOWUP_MAX_LENGTH, TEST_RUN_ERROR_MAX_LENGTH
        )
        self.assertLessEqual(len(settings.SUPPORT_FOLLOWUP), settings.SUPPORT_FOLLOWUP_MAX_LENGTH)

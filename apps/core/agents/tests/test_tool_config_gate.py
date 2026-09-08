"""The tools-config boot gate: the kernel parser takes names as
written (only the registries know the rosters), so every naming
mistake in config/tools.toml refuses at boot with the valid options
named, and each refusal here is one an operator can act on without
reading source.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from agents.tools.registry import validate_tool_config


class ToolsCommandTests(SimpleTestCase):
    """manage.py tools: the matrix an operator reads after editing
    the config, computed off the rosters and the live wiring."""

    def test_the_matrix_names_wiring_status_and_alternatives(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with self.settings(
            TOOL_WIRING={"web_search": "duckduckgo", "find_contacts": "serper"},
            TOOL_VENDOR_KEYS={"serper": {"api_key": "k"}},
        ):
            call_command("tools", stdout=out)
        lines = out.getvalue().splitlines()
        self.assertEqual(
            lines,
            [
                "web_search: duckduckgo (ready) | supports: duckduckgo (ready), serper (ready)",
                "find_contacts: serper (ready) | supports: serper (ready)",
            ],
        )


class ToolConfigGateTests(SimpleTestCase):
    def test_a_passing_config_is_silent(self):
        with self.settings(
            TOOL_VENDOR_KEYS={"serper": {"api_key": "k"}},
            TOOL_WIRING={"web_search": "serper", "find_contacts": "serper"},
        ):
            validate_tool_config()

    def test_an_absent_vendor_table_stays_silent(self):
        # Absence is not a mistake: the vendor gates honestly at
        # runtime as not configured.
        with self.settings(TOOL_VENDOR_KEYS={}, TOOL_WIRING={}):
            validate_tool_config()

    def test_an_unknown_vendor_section_refuses_naming_the_roster(self):
        with (
            self.settings(TOOL_VENDOR_KEYS={"mystery": {"token": "t"}}),
            self.assertRaisesMessage(ImproperlyConfigured, "mystery") as caught,
        ):
            validate_tool_config()
        self.assertIn("registered vendors: duckduckgo, serper", str(caught.exception))

    def test_an_empty_table_refuses_naming_the_declared_keys(self):
        # Present-but-empty is a mistake (the operator plainly meant
        # to configure it); absent gates honestly at runtime.
        with (
            self.settings(TOOL_VENDOR_KEYS={"serper": {}}),
            self.assertRaisesMessage(ImproperlyConfigured, "missing or empty api_key") as caught,
        ):
            validate_tool_config()
        self.assertIn("needs exactly: api_key", str(caught.exception))

    def test_a_partial_multi_key_table_names_every_gap(self):
        # Every registered vendor is one-key today, so the gate's
        # multi-key joins ("needs exactly: a, b", "missing or empty
        # b") only stay covered through a synthetic two-key spec.
        from dataclasses import dataclass, replace

        from agents.tools.search.providers import registry as providers_registry
        from agents.tools.search.providers import serper

        @dataclass(frozen=True)
        class TwoKeys:
            login: str
            password: str

        two = replace(serper.SPEC, config_schema=TwoKeys)
        with (
            patch.dict(providers_registry._REGISTRY, {"serper": two}),
            self.settings(TOOL_VENDOR_KEYS={"serper": {"login": "l", "password": ""}}),
            self.assertRaisesMessage(ImproperlyConfigured, "missing or empty password") as caught,
        ):
            validate_tool_config()
        self.assertIn("needs exactly: login, password", str(caught.exception))

    def test_an_empty_credential_reads_as_missing(self):
        with (
            self.settings(TOOL_VENDOR_KEYS={"serper": {"api_key": ""}}),
            self.assertRaisesMessage(ImproperlyConfigured, "missing or empty api_key"),
        ):
            validate_tool_config()

    def test_a_typoed_credential_key_refuses_naming_it(self):
        with (
            self.settings(TOOL_VENDOR_KEYS={"serper": {"api_key": "k", "apikey": "x"}}),
            self.assertRaisesMessage(ImproperlyConfigured, "unknown apikey"),
        ):
            validate_tool_config()

    def test_wiring_an_unknown_tool_refuses_naming_the_tools(self):
        with (
            self.settings(TOOL_WIRING={"mystery_tool": "duckduckgo"}),
            self.assertRaisesMessage(ImproperlyConfigured, "mystery_tool") as caught,
        ):
            validate_tool_config()
        self.assertIn("tools that take a vendor: find_contacts, web_search", str(caught.exception))

    def test_wiring_off_the_roster_refuses_naming_the_supported_vendors(self):
        # The free engine has no contacts adapter judgment behind it;
        # the refusal is the compatibility documentation.
        with (
            self.settings(TOOL_WIRING={"find_contacts": "duckduckgo"}),
            self.assertRaisesMessage(ImproperlyConfigured, "find_contacts supports: serper"),
        ):
            validate_tool_config()

    def test_a_declared_roster_vendor_must_be_registered(self):
        # The membership check spec construction cannot make: vendors
        # register in the same ready() walk as the tools.
        from dataclasses import replace

        from agents.tools import registry as tool_registry
        from agents.tools.search import web_search

        rogue = replace(web_search.SPEC, vendors=("duckduckgo", "zz_missing"))
        with (
            patch.dict(tool_registry._REGISTRY, {"web_search": rogue}),
            self.assertRaisesMessage(ImproperlyConfigured, "zz_missing"),
        ):
            validate_tool_config()

    def test_ready_wires_the_gate(self):
        # The gate is one deleted call line away from silently
        # vanishing; this pins the wiring, not just the refusals.
        import agents
        from agents.apps import AgentsConfig
        from agents.tools import registry as tool_registry

        with patch.object(tool_registry, "validate_tool_config") as gate:
            AgentsConfig("agents", agents).ready()
        gate.assert_called_once()

"""The tools config parser (openbower_kernel): vendor credentials and
tool wiring from ONE file, loud refusal over silently gated tools.
The parser is NAME-AGNOSTIC (any vendor section and any wiring key
parse; a typo'd name refuses at boot through the registry gate, which
knows the rosters), so what it guards is shape alone: every section a
single table of string values.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from openbower_kernel.tool_config import (
    ToolConfig,
    ToolConfigError,
    parse_tool_config,
    resolve_tool_config,
)


class ToolConfigParseTests(SimpleTestCase):
    def test_vendor_tables_and_wiring_split(self):
        text = """
[tools]
web_search = "duckduckgo"
find_contacts = "acme_search"

[acme_search]
login = "l"
password = "p"
"""
        config = parse_tool_config(text)
        self.assertEqual(
            config,
            ToolConfig(
                vendor_keys={"acme_search": {"login": "l", "password": "p"}},
                wiring={"web_search": "duckduckgo", "find_contacts": "acme_search"},
            ),
        )

    def test_any_names_parse(self):
        # The parser does not know the vendor or tool rosters: a name
        # it has never heard of parses as written, and the registry
        # gate's boot validation is what refuses it (its own test
        # lives with the gate).
        config = parse_tool_config('[mystery_vendor]\ntoken = "t"\n\n[tools]\nmystery_tool = "mystery_vendor"\n')
        self.assertEqual(config.vendor_keys, {"mystery_vendor": {"token": "t"}})
        self.assertEqual(config.wiring, {"mystery_tool": "mystery_vendor"})

    def test_an_empty_file_reads_as_nothing_declared(self):
        self.assertEqual(parse_tool_config(""), ToolConfig(vendor_keys={}, wiring={}))

    def test_refusals_are_loud(self):
        with self.assertRaises(ToolConfigError):
            parse_tool_config("not toml [[")
        with self.assertRaises(ToolConfigError) as caught:
            # A bare top-level key is a stranded credential no vendor
            # will ever read.
            parse_tool_config('login = "l"\n')
        self.assertIn("must be a table", str(caught.exception))
        with self.assertRaises(ToolConfigError) as caught:
            parse_tool_config("[acme_search]\nlogin = 3\n")
        self.assertIn("must be a string", str(caught.exception))
        with self.assertRaises(ToolConfigError):
            # A nested table under a vendor is a non-string value too.
            parse_tool_config('[acme_search.extra]\ntoken = "t"\n')
        with self.assertRaises(ToolConfigError) as caught:
            parse_tool_config("[tools]\nweb_search = 3\n")
        self.assertIn("web_search must be a string", str(caught.exception))


class ResolveToolConfigTests(SimpleTestCase):
    """The whole custody as a pure function: the file, or nothing
    (one path; no env mirror, no no-file defaults)."""

    def test_no_file_means_nothing_declared(self):
        self.assertEqual(resolve_tool_config(Path("/nowhere/tools.toml")), ToolConfig(vendor_keys={}, wiring={}))

    def test_the_file_is_the_one_credential_custody(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.toml"
            path.write_text('[acme_search]\nlogin = "l"\npassword = "p"\n')
            self.assertEqual(resolve_tool_config(path).vendor_keys["acme_search"]["password"], "p")

    def test_a_directory_path_names_the_problem_not_a_raw_oserror(self):
        # TOOLS_CONFIG= (set but empty) must resolve to the repo
        # default, but a path that IS a directory still needs OUR
        # error, not a bare IsADirectoryError out of boot.
        with self.assertRaises(ToolConfigError) as caught:
            resolve_tool_config(Path("."))
        self.assertIn("cannot read", str(caught.exception))


class TemplateParityTests(SimpleTestCase):
    """The parser rejects shapes it cannot use, so a template it
    cannot parse would crash every copied config at boot; the
    template and the parser must move together."""

    _TEMPLATE = Path(__file__).resolve().parents[4] / "config" / "templates" / "tools.example.toml"

    def test_the_template_wiring_is_each_tools_declared_default(self):
        # The template's live [tools] section IS the defaults, derived
        # from the rosters, so a roster change moves the template in
        # the same commit or fails here.
        from agents.tools import registry as tool_registry
        from agents.tools.search.machinery import SearchToolSpec

        config = parse_tool_config(self._TEMPLATE.read_text())
        heads = {t.name: t.vendors[0] for t in tool_registry.all_tools() if isinstance(t, SearchToolSpec)}
        self.assertEqual(config.wiring, heads)

    def test_the_template_teaches_each_tools_supported_vendors(self):
        # The comments are the operator's compatibility table, pinned
        # to the live rosters so they cannot drift into folklore; the
        # rosters themselves are computed, never hand-listed anywhere.
        from agents.tools import registry as tool_registry
        from agents.tools.search.machinery import SearchToolSpec

        text = self._TEMPLATE.read_text()
        for tool in tool_registry.all_tools():
            if isinstance(tool, SearchToolSpec):
                with self.subTest(tool=tool.name):
                    self.assertIn(f"# {tool.name} supports: {', '.join(tool.vendors)}", text)

    def test_the_template_ships_no_credentials(self):
        # Credential tables in the template stay commented guidance:
        # a live table would seed every fresh deploy with a fake
        # secret that reads as configured.
        self.assertEqual(parse_tool_config(self._TEMPLATE.read_text()).vendor_keys, {})

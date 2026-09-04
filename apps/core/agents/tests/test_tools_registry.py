"""The tool registry's guards: registration is the ONE write path, and
an invalid or colliding spec must refuse LOUDLY at register() rather
than boot into a quiet gap a row discovers later.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from dataclasses import replace

from django.test import SimpleTestCase

from agents.tools import registry
from agents.tools.base import FailureMode, ToolError
from agents.tools.search import web_search
from openbower_schema.agents import AgentConfig, AgentTools


def _config(**tools) -> AgentConfig:
    return AgentConfig(
        prompt="p",
        provider="openai_compatible",
        source="s",
        model="m",
        tools=tools,
        outputs=[{"key": "value", "label": "Value", "type": "text"}],
    )


class RegisterGuardTests(SimpleTestCase):
    """Every guard FIRES, and fires BEFORE the registry mutates: the
    built-ins registered at import must survive every refusal below."""

    def tearDown(self) -> None:
        # Each refusal must have left the registry exactly as the
        # import-time registrations built it.
        self.assertEqual({spec.name for spec in registry.all_tools()}, set(AgentTools.model_fields))

    def test_reregistering_the_same_spec_is_idempotent(self):
        registry.register(web_search.SPEC)

    def test_a_non_integer_blame_order_refuses(self):
        # Blame order is a DECLARED fact (the walk order carries
        # nothing); a spec without a real one must refuse at
        # registration, not sort as a surprise.
        with self.assertRaisesMessage(ValueError, "blame_order"):
            registry.register(replace(web_search.SPEC, blame_order=True))

    def test_a_different_spec_on_a_taken_name_collides_loudly(self):
        with self.assertRaisesMessage(ValueError, "already registered"):
            registry.register(replace(web_search.SPEC, display_name="Other web search"))

    def test_a_nameless_function_refuses(self):
        # The name IS the function's name; a lambda's "<lambda>" is not
        # a callable name a model could be told to use.
        nameless = replace(web_search.SPEC, function=lambda ctx, query: "")
        with self.assertRaisesMessage(ValueError, "identifier"):
            registry.register(nameless)

    def test_labels_cascade_from_the_name(self):
        # record_label defaults to the name; display_name to the name
        # prettified. Construction-time, so every reader sees the
        # resolved values.
        def sample_tool(ctx, query):
            return ""

        spec = replace(web_search.SPEC, function=sample_tool, record_label="", display_name="")
        self.assertEqual(spec.name, "sample_tool")
        self.assertEqual(spec.record_label, "sample_tool")
        self.assertEqual(spec.display_name, "Sample tool")

    def test_an_empty_failure_vocabulary_refuses(self):
        # The declared error classes ARE the tool's failure
        # vocabulary; a tool claiming it never fails is a spec gap,
        # not a property any provider-backed tool has.
        with self.assertRaisesMessage(ValueError, "failure vocabulary"):
            registry.register(replace(web_search.SPEC, errors=()))

    def test_open_may_not_be_an_error_code(self):
        # "open" is the one reserved status (toggled-and-healthy) and
        # never a failure a class may claim.
        class Open(ToolError):
            code = "open"
            mode = FailureMode.HAZARD

        with self.assertRaisesMessage(ValueError, "open"):
            registry.register(replace(web_search.SPEC, errors=(*web_search.SPEC.errors, Open)))

    def test_family_membership_is_transitive(self):
        # A member specialized from another member is raisable
        # wherever its parent is, so it must be a declared member too:
        # FAILS if family_errors goes back to direct subclasses only
        # (a grandchild would be raisable-but-undeclared, with no code,
        # mode, or closer registered).
        from agents.tools.base import family_errors

        class Base(ToolError):
            pass

        class Parent(Base):
            code = "parent_code"
            mode = FailureMode.HAZARD

        class Child(Parent):
            code = "child_code"
            mode = FailureMode.TRANSIENT

        self.assertEqual(family_errors(Base), (Parent, Child))

    def test_a_tool_without_a_crash_code_refuses(self):
        # The harness folds an unexpected crash as the tool's own
        # error code, unconditionally: a spec that declared none would
        # have its crashes recorded nowhere and read as honest
        # declines. FAILS if the fold goes conditional again.
        without_error = tuple(e for e in web_search.SPEC.errors if e.code != "error")
        with self.assertRaisesMessage(ValueError, "crash fold"):
            registry.register(replace(web_search.SPEC, errors=without_error))

    def test_a_family_spec_declares_its_check_and_note_together(self):
        # SearchToolSpec completeness fires at CONSTRUCTION, the same
        # loud-early rule as the registry: a hit check without its
        # emptied-pool note (or the note without the check) is half a
        # declaration. Provider needs no guard: "" is a meaningful
        # declaration (the configured switch serves).
        from dataclasses import replace

        from agents.tools.search.find_contacts import SPEC as CONTACTS_SPEC

        with self.assertRaisesMessage(ValueError, "together"):
            replace(CONTACTS_SPEC, rejected_note="")
        with self.assertRaisesMessage(ValueError, "together"):
            replace(CONTACTS_SPEC, check_hit=None)

    def test_the_scope_test_derives_both_halves_from_the_pattern(self):
        # ONE declared string serves the query and the return-side
        # test: root and subdomain hosts pass, a lookalike SUFFIX
        # domain fails (the leading dot is the spoof guard), and the
        # path must sit under the scope's path.
        from agents.tools.search.machinery import scope_test
        from agents.tools.search.providers.base import SearchHit

        check = scope_test("acme.com/team")
        cases = {
            "https://acme.com/team/jane": True,
            "https://www.acme.com/team/jane": True,
            "https://br.acme.com/team/jane": True,
            "https://evilacme.com/team/jane": False,
            "https://acme.com/jobs/1": False,
            "https://other.io/team/jane": False,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertIs(check(SearchHit("t", url, "s")), expected)

    def test_a_shared_code_must_carry_the_same_failure_mode(self):
        # web_search declares rate_limited TRANSIENT; a second tool
        # re-meaning the same code would make one detail string behave
        # two ways depending on which tool wrote it.
        class Conflicting(ToolError):
            code = "rate_limited"
            mode = FailureMode.FATAL

        conflicting = (*(e for e in web_search.SPEC.errors if e.code != "rate_limited"), Conflicting)
        with self.assertRaisesMessage(ValueError, "rate_limited"):
            registry.register(replace(web_search.SPEC, errors=conflicting))


class RegistryContentPins(SimpleTestCase):
    def test_the_builtins_cover_the_wire_tool_set(self):
        # The registry and the wire's AgentTools shape are two homes
        # for one roster; drift fails here, not in a fill.
        self.assertEqual({spec.name for spec in registry.all_tools()}, set(AgentTools.model_fields))

    def test_closers_derive_from_the_modes(self):
        # Within-run closing policy is DERIVED (every non-hazard failure
        # closes), pinned to the behavior the old declared set had.
        for tool in registry.all_tools():
            self.assertEqual(tool.closers, frozenset({"not_configured", "rate_limited"}))

    def test_toggled_order_is_the_blame_order(self):
        # Blame for a blank goes to the FIRST toggled tool that
        # closed unserved; registration order is that order, pinned
        # against the wire shape's field order.
        toggled = registry.toggled_tools(_config(web_search=True, find_contacts=True))
        self.assertEqual([spec.name for spec in toggled], list(AgentTools.model_fields))

    def test_toggles_select_specs_by_name(self):
        self.assertEqual(
            [spec.name for spec in registry.toggled_tools(_config(find_contacts=True))],
            ["find_contacts"],
        )
        self.assertEqual(registry.toggled_tools(_config()), [])

    def test_a_toggle_for_an_unregistered_tool_is_loud(self):
        # Unreachable through the closed wire shape today (the pin
        # above holds the two rosters equal), which is why the drift
        # case must raise instead of silently skipping the toggle.
        class _Tools:
            @staticmethod
            def model_dump() -> dict[str, bool]:
                return {"web_search": True, "rogue_tool": True}

        class _Config:
            tools = _Tools()

        with self.assertRaisesMessage(registry.UnknownTool, "rogue_tool"):
            registry.toggled_tools(_Config())

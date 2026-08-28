"""Runtime edges not reachable through the endpoint tests: the
registry's loud refusals (unrunnable addresses raise), roster faith,
and promotion's cap admission.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from agents.models import Agent
from agents.providers import ModelUnavailable, model_for
from agents.services import AgentService, AgentsFull

from .sources import source


class ModelForTests(SimpleTestCase):
    def test_unknown_provider_raises(self):
        # An unrunnable ADDRESS is a config error, not per-row garnish:
        # it fails every cell identically, so it fails loudly up front.
        with self.assertRaises(ModelUnavailable):
            model_for("mystery", "local", "m")

    def test_closed_canonical_source_raises(self):
        with (
            self.settings(OPENAI_COMPATIBLE_SOURCES=source("openai", "https://api.openai.com/v1")),
            self.assertRaises(ModelUnavailable) as caught,
        ):
            model_for("openai_compatible", "openai", "gpt-6")
        self.assertIn("source unknown or closed", str(caught.exception))

    _LOCAL = source("local", "http://o.test/v1")

    def _with_roster(self, names):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"data": [{"id": n, "created": 1} for n in names]}

        return patch("agents.providers.base.httpx.get", return_value=FakeResponse())

    def setUp(self) -> None:
        from agents.providers import openai_compatible

        openai_compatible.DOOR._roster_cache.clear()
        self.addCleanup(openai_compatible.DOOR._roster_cache.clear)

    def test_open_source_yields_a_runnable_model(self):
        with self.settings(OPENAI_COMPATIBLE_SOURCES=self._LOCAL), self._with_roster(["gemma4:12b"]):
            model = model_for("openai_compatible", "local", "gemma4:12b")
        self.assertIsNotNone(model)
        self.assertEqual(model.model_name, "gemma4:12b")

    def test_a_model_off_the_probed_roster_raises(self):
        with (
            self.settings(OPENAI_COMPATIBLE_SOURCES=self._LOCAL),
            self._with_roster(["gemma4:12b"]),
            self.assertRaises(ModelUnavailable),
        ):
            model_for("openai_compatible", "local", "gemma5:huge")

    def test_an_unprobeable_roster_is_taken_on_faith(self):
        # Running must not hard-depend on /models uptime: an empty or
        # failed probe defers the wrong-name failure to completion time.
        with (
            self.settings(OPENAI_COMPATIBLE_SOURCES=self._LOCAL),
            patch("agents.providers.base.httpx.get", side_effect=ConnectionError("down")),
        ):
            model = model_for("openai_compatible", "local", "anything")
        self.assertEqual(model.model_name, "anything")


class PromotionTests(TestCase):
    _IDS = {"account_id": "01AC" + "A" * 22, "user_id": "01US" + "A" * 22}

    def _ephemeral(self) -> Agent:
        return Agent.objects.create(
            **self._IDS,
            label="quick",
            provider="openai_compatible",
            source="local",
            model="m",
            prompt="p",
            outputs=[{"key": "a", "label": "A", "type": "text", "description": ""}],
            ephemeral=True,
        )

    def test_promotion_is_a_flag_flip_with_a_name(self):
        service = AgentService(**self._IDS)
        promoted = service.promote(self._ephemeral(), label="Kept")
        self.assertFalse(promoted.ephemeral)
        self.assertEqual(promoted.label, "Kept")
        self.assertEqual([a.label for a in service.list()], ["Kept"])

    def test_promotion_is_a_roster_admission(self):
        # The cap must hold at the flag flip exactly as at create.
        service = AgentService(**self._IDS)
        row = self._ephemeral()
        with patch("agents.services.agents.MAX_AGENTS", 0), self.assertRaises(AgentsFull):
            service.promote(row, label="Over")
        row.refresh_from_db()
        self.assertTrue(row.ephemeral)


class ToolPoolTests(SimpleTestCase):
    """The toolset's pooling edges: canonical dedupe and the authored
    -query clamp, exercised without a framework run."""

    class _Ctx:
        def __init__(self, deps):
            self.deps = deps

    def test_hits_dedupe_by_canonical_url_across_calls(self):
        from agents.runtime.tools import CellDeps, web_search
        from agents.search import SearchHit, SearchOutcome

        deps = CellDeps()
        hits = [
            SearchHit("A", "https://www.acme.com/x", "s"),
            SearchHit("B", "https://acme.com/x/", "s"),
        ]
        with patch(
            "agents.runtime.tools.search",
            return_value=SearchOutcome("q", hits, failed=False),
        ):
            web_search(self._Ctx(deps), "acme")
        self.assertEqual(len(deps.evidence), 1)

    def test_the_non_spending_legs_never_buy_a_search(self):
        # An empty query and a repeated exact query are loop behavior,
        # not new intent: the metered budget is for QUERIES, so both
        # must answer from what the pool already holds.
        from agents.runtime.tools import CellDeps, find_contacts, web_search
        from agents.search import SearchOutcome

        deps = CellDeps()
        with patch("agents.runtime.tools.search", return_value=SearchOutcome("acme ceo", [], failed=False)):
            web_search(self._Ctx(deps), "acme ceo")
        with patch("agents.runtime.tools.search") as searched:
            web_search(self._Ctx(deps), "")
            web_search(self._Ctx(deps), "acme ceo")
            find_contacts(self._Ctx(deps), "")
        searched.assert_not_called()

    def test_an_exhausted_rate_limit_closes_the_door_for_the_run(self):
        # The closed note never says "answer from the records already
        # gathered"; every later call (either tool) is refused BEFORE
        # the spend and leaves no outcome, so the stored searches show
        # only what hit the wire.
        import json

        from agents.runtime.tools import NOTE_DOOR_CLOSED, CellDeps, find_contacts, web_search
        from agents.search import SearchOutcome

        deps = CellDeps()
        exhausted = SearchOutcome("acme", [], failed=True, cause="rate_limited", provider="duckduckgo", attempts=5)
        with patch("agents.runtime.tools.search", return_value=exhausted):
            first = json.loads(web_search(self._Ctx(deps), "acme"))
        self.assertEqual(first, {"records": [], "note": NOTE_DOOR_CLOSED})
        self.assertNotIn("answer from", NOTE_DOOR_CLOSED)
        self.assertEqual(deps.door_closed, "web_search")
        with patch("agents.runtime.tools.search") as searched:
            second = json.loads(web_search(self._Ctx(deps), "acme inc"))
            third = json.loads(find_contacts(self._Ctx(deps), "VP Sales Acme"))
        searched.assert_not_called()
        self.assertEqual(second["note"], NOTE_DOOR_CLOSED)
        self.assertEqual(third["note"], NOTE_DOOR_CLOSED)
        self.assertEqual(len(deps.outcomes), 1)

    def test_a_timeout_or_error_leaves_the_door_open(self):
        import json

        from agents.runtime.tools import NOTE_FAILED, CellDeps, web_search
        from agents.search import SearchOutcome

        deps = CellDeps()
        for cause in ("timeout", "error"):
            with (
                self.subTest(cause=cause),
                patch("agents.runtime.tools.search", return_value=SearchOutcome("q " + cause, [], True, cause)),
            ):
                note = json.loads(web_search(self._Ctx(deps), "q " + cause))["note"]
                self.assertEqual(note, NOTE_FAILED)
                self.assertEqual(deps.door_closed, "")
        self.assertEqual(len(deps.outcomes), 2)

    def test_model_authored_queries_clamp(self):
        from agents.constants import QUERY_MAX_LENGTH
        from agents.runtime.tools import CellDeps, web_search
        from agents.search import SearchOutcome

        seen: list[str] = []

        def fake_search(query, **kwargs):
            seen.append(query)
            return SearchOutcome(query, [], failed=False)

        # The clamp is LOGGED: a cut query is a different question than
        # the model asked, and silence would hide a model that keeps
        # overrunning the bound.
        with (
            patch("agents.runtime.tools.search", side_effect=fake_search),
            self.assertLogs("agents.runtime.tools", level="WARNING") as logs,
        ):
            web_search(self._Ctx(CellDeps()), "q" * (QUERY_MAX_LENGTH + 64))
        self.assertEqual(len(seen[0]), QUERY_MAX_LENGTH)
        self.assertIn(f"web_search query truncated from {QUERY_MAX_LENGTH + 64} to {QUERY_MAX_LENGTH}", logs.output[0])
        with patch("agents.runtime.tools.search", side_effect=fake_search), self.assertNoLogs("agents.runtime.tools"):
            web_search(self._Ctx(CellDeps()), "q" * QUERY_MAX_LENGTH)


class RenderPromptTests(SimpleTestCase):
    """The sandboxed template engine: full Django semantics over the
    row's strings, none of the reach a prompt must not have."""

    def test_variables_filters_and_conditionals_render(self):
        from agents.runtime.prompts import render_prompt

        rendered = render_prompt(
            "Hi {{ name|upper }}{% if domain %} at {{ domain }}{% endif %}, {{ industry|default:'any industry' }}",
            {"name": "Acme", "domain": "acme.com"},
        )
        self.assertEqual(rendered, "Hi ACME at acme.com, any industry")

    def test_missing_variables_render_blank_and_text_is_not_escaped(self):
        from agents.runtime.prompts import render_prompt

        self.assertEqual(render_prompt("{{ ghost }}!", {}), "!")
        # Prompts are model text, not HTML: autoescape must be off.
        self.assertEqual(render_prompt("Tom & Jerry's {{ name }}", {"name": "<shop>"}), "Tom & Jerry's <shop>")

    def test_underscore_attributes_are_a_parse_error(self):
        from django.template.exceptions import TemplateSyntaxError

        from agents.runtime.prompts import validate_prompt

        with self.assertRaises(TemplateSyntaxError):
            validate_prompt("{{ name.__class__ }}")

    def test_includes_have_no_filesystem_to_reach(self):
        from django.template.exceptions import TemplateDoesNotExist

        from agents.runtime.prompts import render_prompt

        with self.assertRaises(TemplateDoesNotExist):
            render_prompt("{% include 'admin/base.html' %}", {})


class GroundValueTests(SimpleTestCase):
    """The prose-grounding edges: models are primed to bracket URLs
    (evidence lines read "title :: snippet [url]"), so the bracketed
    form must ground exactly like the bare one."""

    _ALLOWED = {"linkedin.com/in/janedoe": "https://www.linkedin.com/in/janedoe"}

    def test_a_bracketed_fabricated_url_is_excised_without_litter(self):
        from agents.runtime.grounding import ground_value

        self.assertEqual(
            ground_value("See [https://made.up/fake] for details", self._ALLOWED),
            "See for details",
        )

    def test_a_bracketed_evidence_url_rewrites_in_place(self):
        from agents.runtime.grounding import ground_value

        self.assertEqual(
            ground_value("Profile: [https://uk.linkedin.com/in/janedoe/]", self._ALLOWED),
            "Profile: [https://www.linkedin.com/in/janedoe]",
        )

    def test_bare_prose_grounding_still_holds(self):
        from agents.runtime.grounding import ground_value

        self.assertEqual(
            ground_value("See https://made.up/fake for details", self._ALLOWED),
            "See for details",
        )


class SchemelessGroundingTests(SimpleTestCase):
    """Small local models drop schemes constantly; a scheme-less
    spelling must ground exactly like the schemed one."""

    def test_schemeless_fabrications_ground_or_go(self):
        from agents.runtime.grounding import ground_value

        self.assertEqual(ground_value("www.made.up/fake", {}), "")
        self.assertEqual(ground_value("Visit acme.com/team for the list", {}), "Visit for the list")

    def test_a_schemeless_echo_rewrites_to_the_source_form(self):
        from agents.runtime.grounding import allowed_urls, ground_value

        allowed = allowed_urls(["https://www.acme.com/team"])
        self.assertEqual(ground_value("acme.com/team", allowed), "https://www.acme.com/team")

    def test_a_bare_domain_without_a_path_is_a_fact_not_a_link(self):
        from agents.runtime.grounding import ground_value

        self.assertEqual(ground_value("Their site is acme.com", {}), "Their site is acme.com")
        # www-led included: judging one spelling of a row-fed domain
        # while passing the other blanks an honest echo.
        self.assertEqual(ground_value("www.acme.com", {}), "www.acme.com")

    def test_ratios_are_not_link_claims(self):
        # The bare-domain branch requires a TLD-shaped final label:
        # "4.5/5" once matched it, and grounding destroyed a correct
        # answer exactly where users trust a blank to mean no evidence.
        from agents.runtime.grounding import ground_value

        self.assertEqual(ground_value("4.5/5", {}), "4.5/5")
        self.assertEqual(ground_value("Scored 3.5/5 overall", {}), "Scored 3.5/5 overall")

    def test_the_pool_is_structural_never_reparsed_from_titles(self):
        from agents.runtime.grounding import allowed_urls

        # A hostile page TITLE containing bracketed URLs contributes
        # nothing: the pool reads only the structured hit URLs.
        allowed = allowed_urls(["https://x.test/real"])
        self.assertEqual(list(allowed.values()), ["https://x.test/real"])


class CanonicalLitterTests(SimpleTestCase):
    def test_query_and_fragment_litter_grounds_to_the_source_form(self):
        from agents.runtime.grounding import allowed_urls, ground_value

        allowed = allowed_urls(["https://acme.com/team"])
        self.assertEqual(ground_value("https://acme.com/team?utm_source=news#top", allowed), "https://acme.com/team")

    def test_meaningful_query_params_name_distinct_resources(self):
        # Stripping the whole query collided watch?v=A with watch?v=B:
        # the second hit vanished from evidence and a model URL
        # rewrote to a DIFFERENT real page (worse than a blank).
        from agents.runtime.grounding import allowed_urls, canonical_url, ground_value

        self.assertNotEqual(canonical_url("https://vid.test/watch?v=A"), canonical_url("https://vid.test/watch?v=B"))
        allowed = allowed_urls(["https://vid.test/watch?v=A"])
        self.assertEqual(ground_value("https://vid.test/watch?v=B", allowed), "")
        self.assertEqual(ground_value("https://vid.test/watch?v=A&utm_medium=x", allowed), "https://vid.test/watch?v=A")


class QueryDedupeTests(SimpleTestCase):
    def test_a_repeated_exact_query_never_respends_the_metered_call(self):
        from unittest.mock import patch as unit_patch

        from agents.runtime.tools import CellDeps, web_search
        from agents.search import SearchHit, SearchOutcome

        class _Ctx:
            def __init__(self, deps):
                self.deps = deps

        deps = CellDeps()
        outcome = SearchOutcome("acme ceo", [SearchHit(title="t", url="https://a.test/x", snippet="s")], failed=False)
        with unit_patch("agents.runtime.tools.search", return_value=outcome) as searched:
            web_search(_Ctx(deps), "acme ceo")
            note = web_search(_Ctx(deps), "acme ceo")
        self.assertEqual(searched.call_count, 1)
        self.assertIn("already searched", note)


class PoolPermissivenessTests(SimpleTestCase):
    def test_row_fed_bare_and_www_domains_enter_the_pool(self):
        # The DETECTOR stays strict, the POOL is permissive: it reads
        # the user's own prompt, and a schemed echo of row-fed ground
        # truth must rewrite to the row's spelling, never blank.
        from agents.runtime.grounding import allowed_urls, ground_value

        allowed = allowed_urls([], "Company site: www.acme.com | domain: example.io")
        self.assertEqual(ground_value("https://www.acme.com", allowed), "www.acme.com")
        self.assertEqual(ground_value("https://example.io/", allowed), "example.io")

    def test_trailing_slash_normalizes_even_with_a_surviving_query(self):
        from agents.runtime.grounding import canonical_url

        self.assertEqual(canonical_url("https://a.test/team/?v=1"), canonical_url("https://a.test/team?v=1"))

    def test_linkedin_litter_is_tracking_not_identity(self):
        from agents.runtime.grounding import canonical_url

        self.assertEqual(
            canonical_url("https://linkedin.com/in/jane?originalSubdomain=uk&trk=feed"),
            canonical_url("https://linkedin.com/in/jane"),
        )
        # SCOPED to the people site: on any other host trk may be a
        # real identity param naming a distinct resource.
        self.assertNotEqual(canonical_url("https://acme.com/x?trk=a"), canonical_url("https://acme.com/x"))

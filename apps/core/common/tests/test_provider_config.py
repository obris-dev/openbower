"""The providers config parser (openbower_kernel): structure from the
file, secrets never in it, loud refusal over a silently empty catalog.
The parser is NAME-AGNOSTIC (any section parses; a typo'd section
refuses at boot through the registry, which knows the roster), so
what it guards is entry shape alone; canonical-ness moved to the
providers' own enrichment via the kernel's vendor_host predicate.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from openbower_kernel.provider_config import (
    ProviderConfigError,
    RawSource,
    make_source,
    parse_provider_sources,
)


class ProviderConfigTests(SimpleTestCase):
    def test_named_sources_parse_per_section(self):
        text = """
[[openai_compatible]]
name = "ollama"
base_url = "http://localhost:11434/v1/"

[[openai_compatible]]
name = "openai"
base_url = "https://api.openai.com/v1"

[[anthropic_compatible]]
name = "anthropic"
base_url = "https://api.anthropic.com"
"""
        sources = parse_provider_sources(text)
        # Trailing slashes normalize; file order is preserved; keys ""
        # unless inline; concurrency 0 unless declared. RAW entries
        # only: canonical-ness is the provider's fact, derived at read
        # against its own vendor origin, never here.
        self.assertEqual(
            sources["openai_compatible"],
            {
                "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "", "concurrency": 0},
                "openai": {"base_url": "https://api.openai.com/v1", "api_key": "", "concurrency": 0},
            },
        )
        self.assertEqual(
            sources["anthropic_compatible"],
            {"anthropic": {"base_url": "https://api.anthropic.com", "api_key": "", "concurrency": 0}},
        )

    def test_any_section_name_parses(self):
        # The parser does not know the provider roster: a section it
        # has never heard of parses as written, and the registry's
        # boot validation is what refuses it (its own test lives with
        # the registry).
        sources = parse_provider_sources('[[mystery_spec]]\nname = "x"\nbase_url = "http://x.test"\n')
        self.assertEqual(list(sources), ["mystery_spec"])

    def test_inline_keys_parse(self):
        text = """
[[openai_compatible]]
name = "openai"
base_url = "https://api.openai.com/v1"
api_key = "sk-inline"
"""
        parsed = parse_provider_sources(text)["openai_compatible"]["openai"]
        self.assertEqual(parsed["api_key"], "sk-inline")

    def test_an_empty_file_reads_as_no_sections(self):
        self.assertEqual(parse_provider_sources(""), {})

    def test_refusals_are_loud(self):
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources("not toml [[")
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources('[[openai_compatible]]\nname = "x"\n')  # no base_url
        with self.assertRaises(ProviderConfigError):
            # A non-list top-level value is malformed for EVERY key,
            # registry-known or not.
            parse_provider_sources("openai_compatible = 3\n")
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources(
                '[[openai_compatible]]\nname = "a"\nbase_url = "http://x"\n'
                '[[openai_compatible]]\nname = "a"\nbase_url = "http://y"\n'
            )


class ResolveProviderSourcesTests(SimpleTestCase):
    """The whole custody as a pure function: the file, or nothing
    (one path; no env mirror, no no-file defaults)."""

    def test_no_file_means_no_sources(self):
        # ONE custody: no env-key mirror, no no-file defaults (the
        # dev loop's starting entry comes from make seeding this very
        # file off the template, never from a profile).
        from openbower_kernel.provider_config import resolve_provider_sources

        self.assertEqual(resolve_provider_sources(Path("/nowhere/providers.toml")), {})

    def test_the_file_is_the_one_key_custody(self):
        import tempfile

        from openbower_kernel.provider_config import resolve_provider_sources

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                '[[openai_compatible]]\nname = "openai"\nbase_url = "https://api.openai.com/v1"\napi_key = "inline"\n'
            )
            resolved = resolve_provider_sources(path)
            self.assertEqual(resolved["openai_compatible"]["openai"]["api_key"], "inline")


class EntryStrictnessTests(SimpleTestCase):
    def test_a_typoed_entry_key_refuses_loudly(self):
        with self.assertRaises(ProviderConfigError) as caught:
            parse_provider_sources('[[openai_compatible]]\nname = "x"\nbase_url = "https://a.test"\napikey = "k"\n')
        self.assertIn("apikey", str(caught.exception))

    def test_a_non_http_base_refuses_loudly(self):
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources('[[openai_compatible]]\nname = "x"\nbase_url = "ftp://a.test"\n')

    def test_case_colliding_source_names_refuse(self):
        with self.assertRaises(ProviderConfigError) as caught:
            parse_provider_sources(
                '[[openai_compatible]]\nname = "local"\nbase_url = "http://a.test"\n'
                '[[openai_compatible]]\nname = "Local"\nbase_url = "http://b.test"\n'
            )
        self.assertIn("case-insensitive", str(caught.exception))

    def test_an_overlong_source_name_refuses_at_parse(self):
        from openbower_kernel.provider_config import SOURCE_NAME_MAX_LENGTH

        name = "a" * (SOURCE_NAME_MAX_LENGTH + 1)
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources(f'[[openai_compatible]]\nname = "{name}"\nbase_url = "http://a.test"\n')

    def test_a_directory_path_names_the_problem_not_a_raw_oserror(self):
        # PROVIDERS_CONFIG= (set but empty) once resolved to Path(".")
        # and crashed boot with a bare IsADirectoryError.
        from openbower_kernel.provider_config import resolve_provider_sources

        with self.assertRaises(ProviderConfigError) as caught:
            resolve_provider_sources(Path("."))
        self.assertIn("cannot read", str(caught.exception))


class ConcurrencyKeyTests(SimpleTestCase):
    """The optional per-source fill ceiling: the operator's declared
    integer, bounded 1..MAX_FILL_CONCURRENCY; absent parses as 0
    (undeclared, the custody-class default applies)."""

    def _entry(self, line: str) -> str:
        return f'[[openai_compatible]]\nname = "openai"\nbase_url = "https://api.openai.com/v1"\n{line}\n'

    def test_a_declared_ceiling_round_trips_as_int(self):
        parsed = parse_provider_sources(self._entry("concurrency = 4"))["openai_compatible"]["openai"]
        self.assertEqual(parsed["concurrency"], 4)

    def test_the_bounds_are_inclusive(self):
        from openbower_kernel.provider_config import MAX_FILL_CONCURRENCY

        for value in (1, MAX_FILL_CONCURRENCY):
            with self.subTest(value=value):
                parsed = parse_provider_sources(self._entry(f"concurrency = {value}"))["openai_compatible"]["openai"]
                self.assertEqual(parsed["concurrency"], value)

    def test_undeclared_parses_as_zero(self):
        parsed = parse_provider_sources(self._entry(""))["openai_compatible"]["openai"]
        self.assertEqual(parsed["concurrency"], 0)

    def test_out_of_range_and_wrong_type_values_refuse_naming_source_and_range(self):
        from openbower_kernel.provider_config import MAX_FILL_CONCURRENCY

        # 0 is not a declarable ceiling (it is the undeclared value);
        # true must not slip through as 1 via bool's int subclassing.
        for value in ("0", "65", "-1", '"4"', "1.5", "true"):
            with self.subTest(value=value):
                with self.assertRaises(ProviderConfigError) as caught:
                    parse_provider_sources(self._entry(f"concurrency = {value}"))
                self.assertIn("openai", str(caught.exception))
                self.assertIn(str(MAX_FILL_CONCURRENCY), str(caught.exception))

    def test_unknown_keys_still_refuse(self):
        with self.assertRaises(ProviderConfigError) as caught:
            parse_provider_sources(self._entry("concurency = 4"))
        self.assertIn("concurency", str(caught.exception))

    def test_the_example_template_parses_with_its_ceilings(self):
        # The parser rejects unknown keys, so a template key it cannot
        # parse would crash every copied config at boot; the template
        # and the parser must move together. Only the self-hosted
        # ollama entry ships live (the managed options are commented
        # guidance).
        template = Path(__file__).resolve().parents[4] / "config" / "templates" / "providers.example.toml"
        sources = parse_provider_sources(template.read_text(encoding="utf-8"))
        self.assertEqual(sources["openai_compatible"]["ollama"]["concurrency"], 1)

    def test_the_template_sections_are_registered_providers(self):
        # Sections are provider names now, validated at boot by the
        # registry: a template section the registry does not know
        # would refuse every copied config at start. Subset, not
        # equality: a template need not exercise every provider.
        from agents.providers.registry import provider_names

        template = Path(__file__).resolve().parents[4] / "config" / "templates" / "providers.example.toml"
        sources = parse_provider_sources(template.read_text(encoding="utf-8"))
        self.assertLessEqual(set(sources), set(provider_names()))


class UnparsableBaseTests(SimpleTestCase):
    """A base_url the URL parser itself rejects. The scheme prefix
    check passes it, so the parser is where a bare ValueError would
    escape: settings import catches only ProviderConfigError, and a
    raw error out of boot names nothing an operator can fix."""

    def test_an_unparsable_base_refuses_as_OUR_error(self):
        for base in ("http://[oops/v1", "https://[::1/v1"):
            with self.subTest(base=base):
                with self.assertRaises(ProviderConfigError) as caught:
                    parse_provider_sources(f'[[openai_compatible]]\nname = "b"\nbase_url = "{base}"\n')
                self.assertIn("b", str(caught.exception))


class SourceConstructionTests(SimpleTestCase):
    """Every RawSource reaches a consumer through make_source, from
    either caller: the operator's file or a fixture.

    The zero-config Ollama seed was a dict literal for a while. When
    a field joined the shape, the literal did not gain it, and the
    first catalog read on the documented zero-config path
    (DJANGO_ENV=local with no providers.toml) raised KeyError. These
    pin the constructor as the only way in, so a new field cannot
    strand a seed the same way. (The ENRICHED shape's completeness
    pin lives with the providers, beside full_source.)"""

    def test_a_constructed_source_carries_every_declared_field(self):
        source = make_source(base_url="http://localhost:11434/v1")
        self.assertEqual(set(source), set(RawSource.__annotations__))

    def test_the_parser_and_a_seed_produce_the_same_shape(self):
        parsed = parse_provider_sources(
            """
[[openai_compatible]]
name = "ollama"
base_url = "http://localhost:11434/v1"
"""
        )["openai_compatible"]["ollama"]
        seeded = make_source(base_url="http://localhost:11434/v1")
        self.assertEqual(set(parsed), set(seeded))
        self.assertEqual(parsed, seeded)

"""The providers config parser (openbower_kernel): structure from the
file, secrets never in it, loud refusal over a silently empty catalog.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from openbower_kernel.provider_config import ProviderConfigError, parse_provider_sources


class ProviderConfigTests(SimpleTestCase):
    def test_named_sources_parse_per_spec(self):
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
        # unless inline.
        self.assertEqual(
            sources["openai_compatible"],
            {
                "ollama": {"base_url": "http://localhost:11434/v1", "api_key": ""},
                "openai": {"base_url": "https://api.openai.com/v1", "api_key": ""},
            },
        )
        self.assertEqual(
            sources["anthropic_compatible"], {"anthropic": {"base_url": "https://api.anthropic.com", "api_key": ""}}
        )

    def test_inline_keys_parse(self):
        text = """
[[openai_compatible]]
name = "openai"
base_url = "https://api.openai.com/v1"
api_key = "sk-inline"
"""
        parsed = parse_provider_sources(text)["openai_compatible"]["openai"]
        self.assertEqual(parsed["api_key"], "sk-inline")

    def test_missing_specs_read_as_empty(self):
        self.assertEqual(parse_provider_sources("")["openai_compatible"], {})

    def test_refusals_are_loud(self):
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources("not toml [[")
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources('[[openai_compatible]]\nname = "x"\n')  # no base_url
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources('[[mystery_spec]]\nname = "x"\nbase_url = "http://x"\n')
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources(
                '[[openai_compatible]]\nname = "a"\nbase_url = "http://x"\n'
                '[[openai_compatible]]\nname = "a"\nbase_url = "http://y"\n'
            )


class ResolveProviderSourcesTests(SimpleTestCase):
    """The whole custody as a pure function: the file, or every spec
    empty (one path; no env mirror, no no-file defaults)."""

    def test_no_file_means_no_sources(self):
        # ONE custody: no env-key mirror, no no-file defaults (the
        # local profile's keyless ollama seed is a profile default,
        # not provider custody).
        from openbower_kernel.provider_config import resolve_provider_sources

        resolved = resolve_provider_sources(Path("/nowhere/providers.toml"))
        self.assertEqual(resolved, {"openai_compatible": {}, "anthropic_compatible": {}})

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
        from openbower_kernel.provider_config import ProviderConfigError, parse_provider_sources

        with self.assertRaises(ProviderConfigError) as caught:
            parse_provider_sources('[[openai_compatible]]\nname = "x"\nbase_url = "https://a.test"\napikey = "k"\n')
        self.assertIn("apikey", str(caught.exception))

    def test_a_non_http_base_refuses_loudly(self):
        from openbower_kernel.provider_config import ProviderConfigError, parse_provider_sources

        with self.assertRaises(ProviderConfigError):
            parse_provider_sources('[[openai_compatible]]\nname = "x"\nbase_url = "ftp://a.test"\n')

    def test_case_colliding_source_names_refuse(self):
        from openbower_kernel.provider_config import ProviderConfigError, parse_provider_sources

        with self.assertRaises(ProviderConfigError) as caught:
            parse_provider_sources(
                '[[openai_compatible]]\nname = "local"\nbase_url = "http://a.test"\n'
                '[[openai_compatible]]\nname = "Local"\nbase_url = "http://b.test"\n'
            )
        self.assertIn("case-insensitive", str(caught.exception))

    def test_an_overlong_source_name_refuses_at_parse(self):
        from openbower_kernel.provider_config import (
            SOURCE_NAME_MAX_LENGTH,
            ProviderConfigError,
            parse_provider_sources,
        )

        name = "a" * (SOURCE_NAME_MAX_LENGTH + 1)
        with self.assertRaises(ProviderConfigError):
            parse_provider_sources(f'[[openai_compatible]]\nname = "{name}"\nbase_url = "http://a.test"\n')

    def test_a_directory_path_names_the_problem_not_a_raw_oserror(self):
        # PROVIDERS_CONFIG= (set but empty) once resolved to Path(".")
        # and crashed boot with a bare IsADirectoryError.
        from openbower_kernel.provider_config import ProviderConfigError, resolve_provider_sources

        with self.assertRaises(ProviderConfigError) as caught:
            resolve_provider_sources(Path("."))
        self.assertIn("cannot read", str(caught.exception))

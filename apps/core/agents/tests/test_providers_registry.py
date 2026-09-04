"""The inference-provider registry's guards: registration is the ONE
write path, and an invalid or colliding declaration must refuse
LOUDLY at register() rather than boot into a quiet gap a fill
discovers later.

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from django.test import SimpleTestCase

from agents.providers import model_timeout_exceptions
from agents.providers import registry as providers_registry
from agents.providers.anthropic_compatible import AnthropicCompatibleProvider
from agents.providers.openai_compatible import PROVIDER as OPENAI_PROVIDER
from agents.providers.openai_compatible import OpenAICompatibleProvider

# The one home for the expected roster (a new provider extends THIS
# list): the walk is alphabetical, so keep it sorted.
_ROSTER = ["anthropic_compatible", "openai_compatible"]


def _variant(**overrides) -> OpenAICompatibleProvider:
    """A throwaway provider subclass carrying one bad declaration:
    providers are classes, so the tools suite's dataclasses.replace
    becomes a subclass with overridden class attrs (a shadowing
    name included: the base derives it from the module, which for
    this module would be the test file's own name)."""
    attrs = {"name": "varianted", "canonical_base": "https://api.example.com/v1"}
    attrs.update(overrides)
    variant_cls = type("VariantProvider", (OpenAICompatibleProvider,), attrs)
    return variant_cls()


class RegisterGuardTests(SimpleTestCase):
    """Every guard FIRES, and fires BEFORE the registry mutates: the
    built-ins registered at ready() must survive every refusal below."""

    def tearDown(self) -> None:
        # Each refusal must have left the registry exactly as the
        # roster walk built it (alphabetical: the directory is the
        # roster).
        self.assertEqual(providers_registry.provider_names(), _ROSTER)

    def test_reregistering_the_same_provider_is_idempotent(self):
        providers_registry.register(OPENAI_PROVIDER)

    def test_a_different_provider_on_a_taken_name_collides_loudly(self):
        with self.assertRaisesMessage(ValueError, "already registered"):
            providers_registry.register(_variant(name="openai_compatible"))

    def test_a_non_identifier_name_refuses(self):
        with self.assertRaisesMessage(ValueError, "valid identifier"):
            providers_registry.register(_variant(name="not a name"))

    def test_an_overlong_name_refuses(self):
        with self.assertRaisesMessage(ValueError, "valid identifier"):
            providers_registry.register(_variant(name="p" * 33))

    def test_a_schemeless_canonical_base_refuses(self):
        with self.assertRaisesMessage(ValueError, "http"):
            providers_registry.register(_variant(canonical_base="api.example.com"))

    def test_an_unparsable_canonical_base_refuses(self):
        with self.assertRaisesMessage(ValueError, "canonical_base"):
            providers_registry.register(_variant(canonical_base="https://[bad"))

    def test_a_hostless_canonical_base_refuses(self):
        # Parsable but hostless: vendor_host("") would compare every
        # source non-canonical, i.e. keyless-OPEN at the vendor, the
        # exact hazard the canonical flag exists to prevent.
        with self.assertRaisesMessage(ValueError, "no host"):
            providers_registry.register(_variant(canonical_base="https:///v1"))

    def test_a_missing_timeout_exception_refuses(self):
        with self.assertRaisesMessage(ValueError, "timeout_exception"):
            providers_registry.register(_variant(timeout_exception=None))

    def test_a_non_exception_timeout_refuses(self):
        with self.assertRaisesMessage(ValueError, "timeout_exception"):
            providers_registry.register(_variant(timeout_exception=str))


class RosterTests(SimpleTestCase):
    def test_the_walk_order_is_deterministic(self):
        # The directory is the roster, walked alphabetically; nothing
        # semantic rides provider order (the picker groups by source).
        self.assertEqual(providers_registry.provider_names(), _ROSTER)

    def test_every_provider_declares_its_timeout(self):
        # One SDK timeout type per registered provider, in the same
        # order; the answerer's ladder catches this tuple.
        exceptions = model_timeout_exceptions()
        self.assertEqual(len(exceptions), len(providers_registry.provider_names()))
        self.assertIn(OpenAICompatibleProvider.timeout_exception, exceptions)
        self.assertIn(AnthropicCompatibleProvider.timeout_exception, exceptions)


class InferenceSourcesLoudnessTests(SimpleTestCase):
    """The boot gate's two refusals: the toml parser takes any
    section as written (only the registry knows the roster), so a
    typo'd section refuses at boot with the section named, and a
    registered provider the wire Literal does not carry refuses the
    same way, naming the missing step."""

    def test_an_unknown_section_refuses_boot(self):
        from django.core.exceptions import ImproperlyConfigured

        with (
            self.settings(INFERENCE_SOURCES={"mystery_spec": {}}),
            self.assertRaisesMessage(ImproperlyConfigured, "mystery_spec"),
        ):
            providers_registry.validate_inference_sources()

    def test_registered_sections_pass_and_absent_ones_stay_silent(self):
        with self.settings(INFERENCE_SOURCES={"openai_compatible": {}}):
            providers_registry.validate_inference_sources()
        with self.settings(INFERENCE_SOURCES={}):
            providers_registry.validate_inference_sources()

    def test_a_registered_provider_missing_from_the_wire_refuses_boot(self):
        # The MIRROR direction: registration is membership, so a
        # drop-in provider that skipped the wire Literal must refuse
        # at boot naming the missing step, never 500 the catalog at
        # request time.
        from unittest.mock import patch

        from django.core.exceptions import ImproperlyConfigured

        rogue = _variant(name="rogue_spec")
        with (
            patch.dict(providers_registry._REGISTRY, {"rogue_spec": rogue}),
            self.settings(INFERENCE_SOURCES={}),
            self.assertRaisesMessage(ImproperlyConfigured, "rogue_spec"),
        ):
            providers_registry.validate_inference_sources()

    def test_ready_wires_the_validator(self):
        # The relocation is one deleted call line away from silently
        # vanishing; this pins the wiring, not just the refusal.
        from unittest.mock import patch

        import agents
        from agents.apps import AgentsConfig

        with patch.object(providers_registry, "validate_inference_sources") as gate:
            AgentsConfig("agents", agents).ready()
        gate.assert_called_once()


class EnrichmentTests(SimpleTestCase):
    """canonical-ness is the PROVIDER's fact, derived at read against
    its declared vendor origin (the parse-time derivation moved here
    when the kernel went name-agnostic): host alone decides, case-insensitively,
    and an unparsable base can never read as the vendor's."""

    def _full(self, base_url: str, **raw):
        from agents.providers.base import full_source
        from openbower_kernel.provider_config import make_source

        return full_source(
            make_source(base_url=base_url, **raw), canonical_base=OpenAICompatibleProvider.canonical_base
        )

    def test_the_vendor_host_reads_canonical_whatever_the_case_or_path(self):
        for base in ("https://API.OPENAI.COM/v1", "https://api.openai.com:443/v1/beta"):
            with self.subTest(base=base):
                self.assertTrue(self._full(base)["canonical"])

    def test_a_self_hosted_base_reads_non_canonical(self):
        self.assertFalse(self._full("http://localhost:11434/v1")["canonical"])

    def test_an_unparsable_base_never_reads_as_the_vendor(self):
        self.assertFalse(self._full("https://[oops/v1")["canonical"])

    def test_the_enriched_shape_is_complete(self):
        # A field added to either half cannot strand the constructor:
        # the enriched dict carries exactly SourceConfig's annotations.
        from agents.providers.base import SourceConfig

        full = self._full("http://localhost:11434/v1", concurrency=1)
        self.assertEqual(set(full), set(SourceConfig.__annotations__))

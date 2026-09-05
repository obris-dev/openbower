"""The search-provider registry's guarded write path and DERIVED
usability: a spec missing a declared fact refuses at register(), and
a provider's status is read off its vendor table in settings, never a
hand-written lambda (so credentials have ONE custody and a blank key
can never open a vendor).

Run: DJANGO_ENV=test uv run python manage.py test agents
"""

from __future__ import annotations

from dataclasses import replace

from django.test import SimpleTestCase, override_settings

from agents.constants import SearchStatus
from agents.tools.search.providers import dataforseo, duckduckgo
from agents.tools.search.providers.registry import provider_status, register

_TABLE = {"dataforseo": {"login": "l", "password": "p"}}


class RegistrationGuardTests(SimpleTestCase):
    """Refusals are all-or-nothing BEFORE the registry mutates; the
    built-ins registered at import must survive every refusal below."""

    def test_the_config_schema_must_be_a_dataclass_of_str_fields(self):
        from dataclasses import dataclass

        with self.assertRaisesMessage(ValueError, "config_schema"):
            register(replace(duckduckgo.SPEC, config_schema=dict))

        @dataclass(frozen=True)
        class Numeric:
            port: int

        with self.assertRaisesMessage(ValueError, "must all be str"):
            register(replace(duckduckgo.SPEC, config_schema=Numeric))

    def test_config_keys_derive_from_the_config_schema(self):
        # The operator's table keys exist ONCE, as the shape's fields;
        # the derived tuple is what the gate, the status derivation,
        # and the template pins all read.
        self.assertEqual(dataforseo.SPEC.config_keys, ("login", "password"))
        self.assertEqual(duckduckgo.SPEC.config_keys, ())

    def test_metered_must_be_a_bool(self):
        with self.assertRaisesMessage(ValueError, "metered"):
            register(replace(duckduckgo.SPEC, metered=1))

    def test_timeout_must_be_a_real_positive_integer(self):
        # bool subclasses int: True must not slip through as 1.
        with self.assertRaisesMessage(ValueError, "timeout_seconds"):
            register(replace(duckduckgo.SPEC, timeout_seconds=True))
        with self.assertRaisesMessage(ValueError, "timeout_seconds"):
            register(replace(duckduckgo.SPEC, timeout_seconds=0))

    def test_display_must_be_declared(self):
        with self.assertRaisesMessage(ValueError, "display"):
            register(replace(duckduckgo.SPEC, display=""))


class FamilySpecDerivationTests(SimpleTestCase):
    """A family tool passes its facts once: the spec derives its
    availability and failure copy from its own name and roster at
    construction, so no construction site restates them."""

    def test_contacts_derives_availability_from_its_wiring(self):
        from agents.tools.contacts.find_contacts import SPEC

        with override_settings(TOOL_VENDOR_KEYS=_TABLE):
            self.assertEqual(SPEC.availability(), SearchStatus.OPEN)
        self.assertEqual(SPEC.availability(), SearchStatus.NOT_CONFIGURED)

    def test_contacts_derived_copy_names_the_serving_vendor_with_no_remedy(self):
        from agents.tools.contacts.find_contacts import SPEC

        copy = SPEC.failure_copy(SearchStatus.NOT_CONFIGURED)
        self.assertEqual(copy.problem, "Finding contacts isn't set up on DataForSEO")
        self.assertEqual(copy.remedy, "")


class DerivedUsabilityTests(SimpleTestCase):
    """Usability = the vendor table carries every declared key. The
    test profile pins TOOL_VENDOR_KEYS empty, so each case opens only
    what it declares."""

    def test_a_keyed_vendor_flips_on_its_table_alone(self):
        self.assertEqual(provider_status(dataforseo.SPEC.name), SearchStatus.NOT_CONFIGURED)
        with override_settings(TOOL_VENDOR_KEYS=_TABLE):
            self.assertEqual(provider_status(dataforseo.SPEC.name), SearchStatus.OPEN)

    def test_a_partial_table_stays_gated(self):
        with override_settings(TOOL_VENDOR_KEYS={"dataforseo": {"login": "l"}}):
            self.assertEqual(provider_status(dataforseo.SPEC.name), SearchStatus.NOT_CONFIGURED)

    def test_a_blank_credential_never_opens_a_vendor(self):
        with override_settings(TOOL_VENDOR_KEYS={"dataforseo": {"login": "l", "password": ""}}):
            self.assertEqual(provider_status(dataforseo.SPEC.name), SearchStatus.NOT_CONFIGURED)

    def test_a_keyless_vendor_needs_no_table(self):
        self.assertEqual(provider_status(duckduckgo.SPEC.name), SearchStatus.OPEN)

    def test_an_unregistered_name_folds_into_not_configured(self):
        # The wiring is deploy config: a bad value must read as "set
        # this up", never take the app down.
        self.assertEqual(provider_status("mystery"), SearchStatus.NOT_CONFIGURED)

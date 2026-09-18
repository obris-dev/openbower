"""The node-kind registry's guarded write path and the config seam: a
kind class missing a declared fact refuses at register() before the
registry mutates, the boot gate refuses a roster without the kind the
services write, and a node's config is an instance of its kind's class,
dumped on write and parsed back on read.

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_nodes_registry
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from pydantic import ValidationError

from ..constants import NODE_IDENTITY_MAX_LENGTH, NODE_KIND_MAX_LENGTH
from ..models import Node
from ..nodes import registry
from ..nodes.base import NodeConfig
from ..nodes.column_agent import BENCH_IDENTITY, ColumnAgent
from ..nodes.registry import COLUMN_AGENT, all_kinds, parse_config, register, validate_node_kinds
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from ..services.workflows import config_of

AGENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
BOOT_ROSTER = sorted([COLUMN_AGENT, WaitUntil.KIND, Webhook.KIND])


def _roster() -> list[str]:
    return sorted(cls.KIND for cls in all_kinds())


class RegistrationGuardTests(SimpleTestCase):
    """Refusals are all-or-nothing BEFORE the registry mutates; the
    kind registered at boot must survive every refusal below."""

    def tearDown(self) -> None:
        # Each refusal must leave the roster exactly as boot built it.
        self.assertEqual(_roster(), BOOT_ROSTER)

    def test_the_roster_after_boot_is_the_three_kinds(self):
        self.assertEqual(_roster(), BOOT_ROSTER)

    def test_re_registering_the_same_class_is_a_no_op(self):
        register(ColumnAgent)
        self.assertIs(registry.get(COLUMN_AGENT), ColumnAgent)

    def test_a_second_class_under_a_taken_kind_refuses_loud(self):
        class Rival(ColumnAgent):
            pass

        with self.assertRaisesMessage(ValueError, "already registered"):
            register(Rival)
        self.assertIs(registry.get(COLUMN_AGENT), ColumnAgent)

    def test_an_invalid_class_refuses_before_the_registry_mutates(self):
        class BadName(ColumnAgent):
            KIND: ClassVar[str] = "not a name"

        class TooLong(ColumnAgent):
            KIND: ClassVar[str] = "k" * (NODE_KIND_MAX_LENGTH + 1)

        class BlankDisplay(ColumnAgent):
            KIND: ClassVar[str] = "blank_display"
            DISPLAY: ClassVar[str] = ""

        class NoIdentity(NodeConfig):
            KIND: ClassVar[str] = "no_identity"
            DISPLAY: ClassVar[str] = "No identity"

        cases = [
            (BadName, "identifier"),
            (TooLong, "identifier"),
            (BlankDisplay, "DISPLAY"),
            (dict, "NodeConfig subclass"),
            (NoIdentity, "identity projection"),
        ]
        for cls, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaisesMessage(ValueError, fragment):
                register(cls)

    def test_the_boot_gate_refuses_a_roster_without_column_agent(self):
        with patch.dict(registry._REGISTRY, clear=True), self.assertRaisesMessage(ImproperlyConfigured, COLUMN_AGENT):
            validate_node_kinds()
        validate_node_kinds()


class ConfigSeamTests(SimpleTestCase):
    """A node's config is an instance of its kind's class: dumped on
    write, parsed back by the class on read, its identity the class's
    declared projection, bounded once on the base."""

    def test_parse_refuses_an_unknown_kind(self):
        with self.assertRaises(KeyError):
            parse_config("mystery", {})

    def test_parse_refuses_a_malformed_blob(self):
        with self.assertRaises(ValidationError):
            parse_config(COLUMN_AGENT, {"agent_id": ["not", "a", "string"]})

    def test_a_stored_config_round_trips_through_the_node(self):
        config = ColumnAgent(agent_id=AGENT_ID)
        node = Node(kind=config.KIND, config=config.model_dump())
        self.assertEqual(node.config, {"agent_id": AGENT_ID})
        self.assertEqual(config_of(node), config)
        self.assertEqual(ColumnAgent.model_validate(node.config), config)

    def test_identity_is_the_agent_id_and_the_bench_word_for_the_bench(self):
        self.assertEqual(ColumnAgent(agent_id=AGENT_ID).identity(), AGENT_ID)
        # A real word, not blank: a blank identity sits outside the
        # get-or-create key, and the bench is found through that key.
        self.assertEqual(ColumnAgent().identity(), BENCH_IDENTITY)

    def test_the_path_kinds_declare_no_identity(self):
        wait = WaitUntil(inbound_path_ids=["01UP" + "A" * 22])
        webhook = Webhook(destination_id="01DST" + "A" * 21, payload_keys=["company"])
        self.assertEqual((wait.identity(), webhook.identity()), ("", ""))

    def test_the_path_kinds_round_trip_through_the_registry(self):
        for config in (
            WaitUntil(inbound_path_ids=["01UP" + "A" * 22]),
            Webhook(destination_id="01DST" + "A" * 21, payload_keys=["company"], interval_seconds=3600),
        ):
            with self.subTest(kind=config.KIND):
                node = Node(kind=config.KIND, config=config.model_dump())
                self.assertEqual(config_of(node), config)
                self.assertEqual(parse_config(config.KIND, node.config), config)
        self.assertEqual(Webhook(destination_id="d", payload_keys=[]).interval_seconds, 900)

    def test_an_identity_past_the_column_bound_refuses(self):
        with self.assertRaisesMessage(ValueError, "identity exceeds"):
            ColumnAgent(agent_id="a" * (NODE_IDENTITY_MAX_LENGTH + 1)).identity()

    def test_a_non_string_identity_projection_refuses(self):
        class Probe(ColumnAgent):
            KIND: ClassVar[str] = "probe"

            def _identity(self):
                return 7

        with self.assertRaisesMessage(ValueError, "must be a str"):
            Probe().identity()

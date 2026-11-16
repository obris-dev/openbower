"""The wire contract's Literals and the app's enums must name the same
values, or a valid answer fails zod client-side with no server error.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_contract_parity
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from django.test import SimpleTestCase

from openbower_schema.logs import SignalStatus as WireSignalStatus
from signals.constants import LogStatus

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "packages" / "openbower-schema" / "schema.json"


class WireEnumParityTests(SimpleTestCase):
    def test_log_status_parity(self):
        self.assertEqual(set(get_args(WireSignalStatus)), {v.value for v in LogStatus})


def _property_names(node: object) -> set[str]:
    """Every property name anywhere in a JSON Schema."""
    found: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            found.update(properties)
        for value in node.values():
            found |= _property_names(value)
    elif isinstance(node, list):
        for value in node:
            found |= _property_names(value)
    return found


class WireNamesTests(SimpleTestCase):
    def test_the_contract_names_a_signal_s_type_type(self):
        # FAILS if any shape the app serves says the signals service's
        # `signal` (or its catalog's `signals`): the app's wire says type.
        schema = json.loads(_SCHEMA_PATH.read_text())
        self.assertEqual(_property_names(schema) & {"signal", "signals"}, set())

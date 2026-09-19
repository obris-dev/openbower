"""The wire contract's Literals and the Django enums must name the
same values, or valid rows fail zod client-side with no server error.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from lists import constants
from openbower_schema.lists import ColumnType as WireColumnType
from openbower_schema.lists import ListOrigin as WireListOrigin
from openbower_schema.lists import WebhookCellState, WireCellState


class WireEnumParityTests(SimpleTestCase):
    def test_column_type_parity(self):
        self.assertEqual(set(get_args(WireColumnType)), {v.value for v in constants.ColumnType})

    def test_list_origin_parity(self):
        self.assertEqual(set(get_args(WireListOrigin)), {v.value for v in constants.ListOrigin})

    def test_cell_state_parity(self):
        # The wire adds `pending` (derived, never stored) and otherwise
        # names exactly the stored members: a digest's `states` map is
        # typed over the wire vocabulary, so a new stored state must
        # reach it.
        self.assertEqual(set(get_args(WireCellState)) - {"pending"}, {v.value for v in constants.StoredCellState})

    def test_webhook_cell_state_parity(self):
        self.assertEqual(set(get_args(WebhookCellState)), {v.value for v in constants.WebhookCellWord})

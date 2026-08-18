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


class WireEnumParityTests(SimpleTestCase):
    def test_column_type_parity(self):
        self.assertEqual(set(get_args(WireColumnType)), {v.value for v in constants.ColumnType})

    def test_list_origin_parity(self):
        self.assertEqual(set(get_args(WireListOrigin)), {v.value for v in constants.ListOrigin})

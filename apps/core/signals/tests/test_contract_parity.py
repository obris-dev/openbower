"""The wire contract's Literals and the app's enums must name the same
values, or a valid answer fails zod client-side with no server error.

Run: DJANGO_ENV=test uv run python manage.py test signals.tests.test_contract_parity
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from openbower_schema.logs import LogStatus as WireLogStatus
from signals.constants import LogStatus


class WireEnumParityTests(SimpleTestCase):
    def test_log_status_parity(self):
        self.assertEqual(set(get_args(WireLogStatus)), {v.value for v in LogStatus})

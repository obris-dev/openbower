"""The wire contract's Literals and the Django enums must name the
same values, or valid rows fail zod client-side with no server error.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from openbower_schema.webhooks import DeliveryStatusWire, WebhookEnvelopeTypeWire
from webhooks import constants


class WireEnumParityTests(SimpleTestCase):
    def test_envelope_type_parity(self):
        self.assertEqual(set(get_args(WebhookEnvelopeTypeWire)), {v.value for v in constants.WebhookEnvelopeType})

    def test_delivery_status_parity(self):
        self.assertEqual(set(get_args(DeliveryStatusWire)), {v.value for v in constants.DeliveryStatus})

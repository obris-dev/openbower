"""The delivery prune: age alone decides, paged, and the command logs
its count.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import ulid
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from webhooks.constants import WEBHOOK_DELIVERY_MAX_AGE_SECONDS, DeliveryStatus, WebhookEnvelopeType
from webhooks.models import WebhookDelivery
from webhooks.services import WebhookDeliveryService

ACCOUNT = "01AC" + "A" * 22
DESTINATION = "01DS" + "A" * 22


def _delivery(age_seconds: int) -> WebhookDelivery:
    born = timezone.now() - timedelta(seconds=age_seconds)
    return WebhookDelivery.objects.create(
        id=ulid.encode_time(int(born.timestamp() * 1000), 10) + ulid.encode_random(16),
        account_id=ACCOUNT,
        destination_id=DESTINATION,
        type=WebhookEnvelopeType.PING,
        status=DeliveryStatus.OK,
    )


class PruneDeliveriesTests(TestCase):
    def test_old_rows_go_and_young_rows_stay(self):
        old = _delivery(WEBHOOK_DELIVERY_MAX_AGE_SECONDS + 60)
        young = _delivery(60)
        self.assertEqual(WebhookDeliveryService.Global.prune(), 1)
        self.assertFalse(WebhookDelivery.objects.filter(id=old.id).exists())
        self.assertTrue(WebhookDelivery.objects.filter(id=young.id).exists())

    def test_pages_through_a_backlog(self):
        for _ in range(3):
            _delivery(WEBHOOK_DELIVERY_MAX_AGE_SECONDS + 60)
        with patch("webhooks.services.deliveries.DELIVERY_WRITE_BATCH", 2):
            self.assertEqual(WebhookDeliveryService.Global.prune(), 3)
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    def test_command_logs_the_count(self):
        _delivery(WEBHOOK_DELIVERY_MAX_AGE_SECONDS + 60)
        with self.assertLogs("webhooks.management.commands.prune_webhook_deliveries", level="INFO") as logs:
            call_command("prune_webhook_deliveries")
        self.assertIn("purged 1", logs.output[0])

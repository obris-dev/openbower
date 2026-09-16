"""One cron tick: delete webhook deliveries past the retention and
exit. The schedule lives in apps/core/crontab (the compose cron service
runs supercronic over it), never in this process; running it twice is
harmless (the prune is a pure age judgement), so a stuck tick needs no
lock."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...services import WebhookDeliveryService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete webhook deliveries older than the retention (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        purged = WebhookDeliveryService.Global.prune()
        logger.info("prune_webhook_deliveries: purged %d", purged)

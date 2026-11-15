"""One cron tick: delete ingest inbox entries past the retention and
exit. The schedule lives in apps/core/crontab (the compose cron service
runs supercronic over it), never in this process; running it twice is
harmless (the prune is a pure age judgement), so a stuck tick needs no
lock."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...operations.prune_ingest_inbox import PruneIngestInboxOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete ingest inbox entries older than the retention (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        purged = PruneIngestInboxOperation().run()
        logger.info("prune_ingest_inbox: purged %d", purged)

"""One cron tick: purge preview runs past the age baseline and exit. The
schedule lives in apps/core/crontab (the compose cron service runs
supercronic over it), never in this process; running it twice is
harmless (the prune is a pure age judgement), so a stuck tick needs no
lock."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...operations.prune_preview_runs import PrunePreviewRunsOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete preview runs older than the baseline (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        purged = PrunePreviewRunsOperation().run()
        logger.info("prune_preview_runs: purged %d", purged)

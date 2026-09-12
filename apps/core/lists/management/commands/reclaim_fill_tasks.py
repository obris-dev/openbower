"""One cron tick: reclaim fill tasks stuck PROCESSING past the stale
window (a consumer that died mid-run) back to READY, then exit. The
schedule lives in apps/core/crontab (the compose cron service runs
supercronic over it), never in this process. Safe to miss and safe to
double: the reclaim is a pure age judgement (run_cell is timeout-bounded,
so a live run always settles before it looks stale), so a stuck tick
needs no lock.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...services.fill_tasks import FillTaskFlow

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reclaim stale PROCESSING fill tasks back to READY (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        reclaimed = FillTaskFlow.reclaim_stale_processing()
        logger.info("reclaim_fill_tasks: reclaimed %d", reclaimed)

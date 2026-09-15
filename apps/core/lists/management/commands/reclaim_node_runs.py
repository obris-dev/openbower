"""One cron tick: reclaim node runs stuck PROCESSING past the stale
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

from ...services.node_runs import NodeRunFlow

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reclaim stale PROCESSING node runs back to READY (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        reclaimed = NodeRunFlow.reclaim_stale_processing()
        logger.info("reclaim_node_runs: reclaimed %d", reclaimed)

"""One cron tick: reclaim node runs stuck PROCESSING past the stale
window (a consumer that died mid-run) back to READY, and abandon the
queued runs of fills that are no longer open (a stop that died between
its flip and its sweep, a slice that inserted into a fill closing under
it), then exit. The schedule lives in apps/core/crontab (the compose
cron service runs supercronic over it), never in this process. Safe to
miss and safe to double: both are judgements from the state alone (the
reclaim a pure age one, since run_cell is timeout-bounded and a live
run always settles before it looks stale), so a stuck tick needs no
lock.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...services.node_runs import NodeRunFlow

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reclaim stale PROCESSING node runs and abandon a closed fill's queued runs (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        reclaimed = NodeRunFlow.reclaim_stale_processing()
        abandoned = NodeRunFlow.abandon_orphans()
        logger.info("reclaim_node_runs: reclaimed %d, abandoned %d orphaned", reclaimed, abandoned)

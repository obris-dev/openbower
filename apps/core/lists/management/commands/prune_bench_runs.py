"""One cron tick: purge bench runs past the age baseline and exit. The
schedule lives in apps/core/crontab (the compose cron service runs
supercronic over it), never in this process; running it twice is
harmless (the prune is a pure age judgement), so a stuck tick needs no
lock."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...operations.prune_bench_runs import PruneBenchRunsOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete bench runs older than the baseline (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        purged = PruneBenchRunsOperation().run()
        logger.info("prune_bench_runs: purged %d", purged)

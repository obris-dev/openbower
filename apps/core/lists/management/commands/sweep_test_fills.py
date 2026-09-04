"""One cron tick: purge test-kind fills past the age baseline and
exit. The schedule lives in apps/core/crontab (the compose cron
service runs supercronic over it), never in this process; running it twice is harmless (the sweep is a pure age
judgement), so a stuck tick needs no lock."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...operations.sweep_test_fills import SweepTestFillsOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete test-kind fills older than the baseline (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        purged = SweepTestFillsOperation().run()
        logger.info("sweep_test_fills: purged %d", purged)

"""One cron tick: work the background jobs that are due, within the
tick's budget, and exit. The schedule lives in apps/core/crontab (the
compose cron service runs supercronic over it), never in this process.
Safe to miss (a job waits) and safe to double (every transition is a
compare-and-set on the job's status), so a stuck tick needs no lock."""

from __future__ import annotations

import logging
import os
import socket

from django.core.management.base import BaseCommand

from ...services import JobRunner

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Work the background jobs that are due (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        report = JobRunner(worker_id=worker_id).tick()
        logger.info(
            "run_jobs: reclaimed=%d claimed=%d done=%d parked=%d failed=%d",
            report.reclaimed,
            report.claimed,
            report.done,
            report.parked,
            report.failed,
        )

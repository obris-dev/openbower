"""One cron tick: send every webhook node's due rows as one digest
each, settle the runs by what came back, and exit. The schedule lives
in apps/core/crontab (the compose cron service runs supercronic over
it), never in this process. Safe to miss (each run holds its own
window) and safe to double (the claim is a CAS on the run's status),
so a stuck tick needs no lock."""

from __future__ import annotations

import logging
import os
import socket

from django.core.management.base import BaseCommand

from ...operations.flush_webhooks import FlushWebhooksOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send the webhook digests that are due and settle their runs (the compose cron's tick)."

    def handle(self, *args, **options) -> None:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        report = FlushWebhooksOperation(worker_id=worker_id).run()
        logger.info(
            "flush_webhooks: nodes=%d sent=%d parked=%d failed=%d skipped=%d",
            report.nodes,
            report.sent,
            report.parked,
            report.failed,
            report.skipped,
        )

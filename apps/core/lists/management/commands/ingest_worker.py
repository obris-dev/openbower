"""The ingest worker PROCESS: signals and lifecycle, nothing else. The
consume loop and the dedupe/apply handler live in lists/ingest/consumer.py,
collocated with the publisher (the two halves of one bus).

SIGTERM and SIGINT stop after the current message and exit; `--once` drains
until the bus is empty (the CI smoke). Restart-surviving by construction:
state lives in the bus offset and the inbox, never in this process.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...ingest.consumer import IngestConsumer
from ...ingest.topics import LIST_ROWS_INGESTED

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the ingest worker: consume row-push events off the bus, dedupe, apply."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Exit when the bus has no more messages (the CI smoke)."
        )

    def handle(self, *args, **options) -> None:
        if not settings.INGEST_KAFKA_BOOTSTRAP_SERVERS:
            raise CommandError("INGEST_KAFKA_BOOTSTRAP_SERVERS is not set; the ingest worker needs a bus to consume.")
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info(
            "ingest_worker %s up (topic: %s, group: %s)",
            worker_id,
            LIST_ROWS_INGESTED.name,
            LIST_ROWS_INGESTED.consumer_group,
        )
        try:
            IngestConsumer(worker_id=worker_id, stop=self._stop).run(once=options["once"])
        finally:
            logger.info("ingest_worker %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("ingest_worker: signal %s; stopping after the current message", signum)
        self._stop.set()

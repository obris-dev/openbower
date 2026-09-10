"""The autofill worker PROCESS: signals and lifecycle, nothing else. The
drain loop lives in lists/operations/autofill_worker.py, beside the fill
worker's supervisor.

SIGTERM and SIGINT stop after the current row and exit; `--once` drains
until the queue is empty (the CI smoke). Restart-surviving by
construction: state lives in the queue, never in this process.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.core.management.base import BaseCommand

from ...operations.autofill_worker import AutofillWorkerOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the autofill worker: drain the autofill queue for pushed rows."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Exit when the queue is empty (the CI smoke).")

    def handle(self, *args, **options) -> None:
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info("autofill_worker %s up", worker_id)
        try:
            AutofillWorkerOperation(worker_id=worker_id, stop=self._stop).run(once=options["once"])
        finally:
            logger.info("autofill_worker %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("autofill_worker: signal %s; finishing the row in flight", signum)
        self._stop.set()

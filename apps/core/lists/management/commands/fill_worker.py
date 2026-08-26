"""The fill worker PROCESS: signals and lifecycle, nothing else. The
supervisor that interleaves every live fill, and the guard that keeps
one poisoned fill from killing the rest, live in
lists/operations/fill_worker.py, the way the CSV import lives beside
its caller.

SIGTERM and SIGINT finish the rows in flight and exit; `--once` walks
until no fill has claimable work (the CI smoke). Restart-surviving by
construction: state lives in the queue, never in this process."""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.core.management.base import BaseCommand

from ...operations.fill_worker import FillWorkerOperation, paid_search
from ...services.fill_queue import FillQueueService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the fill worker: claim row batches, walk cells, write outcomes."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Exit when no fill is live (the CI smoke).")

    def handle(self, *args, **options) -> None:
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        queue = FillQueueService(worker_id=worker_id)
        logger.info("fill_worker %s up (paid search: %s)", worker_id, paid_search())
        try:
            FillWorkerOperation(queue=queue, stop=self._stop).run(once=options["once"])
        finally:
            logger.info("fill_worker %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("fill_worker: signal %s; finishing rows in flight", signum)
        self._stop.set()

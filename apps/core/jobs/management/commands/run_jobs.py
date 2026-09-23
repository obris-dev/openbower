"""The jobs runner PROCESS: signals and lifecycle, nothing else. The
loop lives in jobs/services/loop.py; the tick in jobs/services/runner.py.

Runs forever as the compose `jobs` service (tick, idle 4 s when nothing
was due, tick), so a job queued by a request is worked within seconds.
SIGTERM and SIGINT stop after the tick in flight. `--once` runs one
tick and exits: the cron shape and the CI smoke. Safe to miss and safe
to double, since every transition is a compare-and-set on the job's
status, so two of these can overlap without harm."""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.core.management.base import BaseCommand

from ...services import JobRunner
from ...services.loop import run_loop

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Work the background jobs as they become due (the compose jobs service; --once for a single tick)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Run one tick and exit (the cron shape, the CI smoke).")

    def handle(self, *args, **options) -> None:
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info("run_jobs %s up", worker_id)
        try:
            run_loop(JobRunner(worker_id=worker_id), stop=self._stop, once=options["once"])
        finally:
            logger.info("run_jobs %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("run_jobs: signal %s; stopping after the tick in flight", signum)
        self._stop.set()

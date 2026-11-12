"""The log poller PROCESS: CLI plumbing over PollLogsOperation (one log per
step, signals/operations/log_poller.py), run as a kernel Daemon.

Runs forever as the compose `log-poller` service: a log is polled the
moment it is due, and the idle (4 s) only follows a step that found none,
so a match the signals service records reaches its log within seconds of
the log's turn. Any number may run: a claim is a compare-and-set lease on
the log. SIGTERM and SIGINT stop after the log in hand. `--once` polls
until no log is due, then exits."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from django.core.management.base import BaseCommand

from openbower_kernel.daemon import Daemon

from ...constants import LOG_POLL_IDLE_SECONDS
from ...operations.log_poller import PollLogsOperation

# A fixed path so the healthcheck can name it; keep the two in sync.
HEARTBEAT_PATH = Path("/tmp/log_poller.heartbeat")


class Command(BaseCommand):
    help = "Fill every log from its listener's matches as they arrive (the compose log-poller; --once to drain)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Poll until no log is due, then exit.")

    def handle(self, *args, **options) -> None:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        poller = PollLogsOperation(worker_id=worker_id)
        daemon = Daemon(
            heartbeat=HEARTBEAT_PATH, idle_seconds=LOG_POLL_IDLE_SECONDS, stdout=self.stdout, stderr=self.stderr
        )
        self.stdout.write(f"log poller up: {worker_id}")
        daemon.run(poller.step, once=options["once"])

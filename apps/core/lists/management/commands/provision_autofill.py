"""The autofill provisioner PROCESS: signals and lifecycle, nothing
else. The provisioning loop lives in
lists/operations/provision/autofill.py.

SIGTERM and SIGINT stop after the current task and exit; `--once` drains
until no READY autofill run remains (the CI smoke). Restart-surviving by
construction: state lives in the task rows and the bus, never in this
process.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...ingest.topics import AUTOFILL_RUNS
from ...operations.provision import AutofillProvisionOperation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the autofill provisioner: publish READY autofill runs to the bus, then mark them QUEUED."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Exit when no READY autofill run remains (CI smoke).")

    def handle(self, *args, **options) -> None:
        if not settings.INGEST_KAFKA_BOOTSTRAP_SERVERS:
            raise CommandError(
                "INGEST_KAFKA_BOOTSTRAP_SERVERS is not set; the autofill provisioner needs a bus to publish to."
            )
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info(
            "provision_autofill %s up (topic: %s, group: %s)",
            worker_id,
            AUTOFILL_RUNS.name,
            AUTOFILL_RUNS.consumer_group,
        )
        try:
            AutofillProvisionOperation(worker_id=worker_id, stop=self._stop).run(once=options["once"])
        finally:
            logger.info("provision_autofill %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("provision_autofill: signal %s; stopping after the current task", signum)
        self._stop.set()

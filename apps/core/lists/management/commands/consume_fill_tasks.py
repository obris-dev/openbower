"""The fill-task processor PROCESS: signals and lifecycle, nothing else.
The consume loop and the claim/run/land handler live in
lists/operations/consume_fill_tasks.py.

SIGTERM and SIGINT stop after the current message and exit; `--once`
drains until the bus is empty (the CI smoke). Restart-surviving by
construction: state lives in the bus offset and the task rows, never in
this process.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...ingest.topics import AUTOFILL_TASKS, FILL_TASKS, TEST_TASKS
from ...operations.consume_fill_tasks import FillTaskConsumer

logger = logging.getLogger(__name__)

# The lane each consumer serves, by name: the automatic firehose, the
# UI-driven (fill-backed) tasks, or the isolated bench-TEST lane. The
# handler routes on the task itself; this only says which bus to read.
_TOPICS = {"autofill": AUTOFILL_TASKS, "manual": FILL_TASKS, "test": TEST_TASKS}


class Command(BaseCommand):
    help = "Run the fill-task processor: consume tasks off the bus, claim, run, land."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Exit when the bus has no more messages (CI smoke).")
        parser.add_argument(
            "--topic",
            choices=sorted(_TOPICS),
            default="autofill",
            help="Which lane to consume (default: autofill). The deploy runs one instance per topic.",
        )

    def handle(self, *args, **options) -> None:
        if not settings.INGEST_KAFKA_BOOTSTRAP_SERVERS:
            raise CommandError(
                "INGEST_KAFKA_BOOTSTRAP_SERVERS is not set; the fill-task processor needs a bus to consume."
            )
        topic = _TOPICS[options["topic"]]
        self._stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._request_stop)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info(
            "consume_fill_tasks %s up (topic: %s, group: %s)",
            worker_id,
            topic.name,
            topic.consumer_group,
        )
        try:
            FillTaskConsumer(worker_id=worker_id, stop=self._stop, topic=topic).run(once=options["once"])
        finally:
            logger.info("consume_fill_tasks %s down", worker_id)

    def _request_stop(self, signum, frame) -> None:
        logger.info("consume_fill_tasks: signal %s; stopping after the current message", signum)
        self._stop.set()

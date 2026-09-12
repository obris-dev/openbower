"""The shared fill-task CONSUMER: the loop that claims tasks off the bus
and hands each to its lane's processor. The consume loop lives here
(mirroring the ingest consumer); the management command owns only signals;
the processing itself lives in services.fill_processing. ONE consumer
serves both lanes, told which topic to read.

`handle_fill_task` is the separable unit, testable without a broker: it
claims the task through the state machine (READY | QUEUED -> PROCESSING,
accepting READY so a message that outran the provisioner's mark still
runs), routes it to AutofillTask or FillBackedTask on its own
`fill_run_id`, and translates a row/list deletion mid-run into a terminal
ROW_MISSING.

The offset is committed AFTER the settle, so a consumer that dies mid-run
redelivers the message and the claim CAS keeps the redelivery from
double-running what already settled.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection

from ..constants import FillTaskStatus
from ..ingest.topics import AUTOFILL_TASKS
from ..services import fill_progress
from ..services.fill_processing import AutofillTask, FillBackedTask
from ..services.fill_tasks import FillTaskFlow
from ..services.lists import ListNotFound, RowNotFound

logger = logging.getLogger(__name__)

# Liveness marker the consume loop refreshes every iteration; the compose
# healthcheck marks the consumer unhealthy when it goes stale, catching a
# WEDGED loop without false-alarming on an idle bus (an idle consumer
# still loops and touches this). A fixed path so the healthcheck can name
# it; keep the two in sync.
_HEARTBEAT_PATH = Path("/tmp/consume_fill_tasks.heartbeat")


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError as e:
        logger.warning("consume_fill_tasks heartbeat write failed: %s", e)


def handle_fill_task(task_id: str, worker_id: str) -> str:
    """Claim one task and hand it to its lane's processor. Returns
    "dropped" (the claim CAS lost: a duplicate delivery, or the reclaim
    scan / another consumer got there first), "done" (settled terminally,
    with or without a value), "parked" (a retriable blank, back to READY
    with a backoff), or "row_missing" / "list_missing" (the row or its list
    vanished). A transient failure inside the run PROPAGATES, so the offset
    is not committed and the message redelivers."""
    flow = FillTaskFlow(worker_id=worker_id)
    task = flow.claim(task_id)
    if task is None:
        return "dropped"
    processor = (FillBackedTask if task.fill_run_id else AutofillTask)(task=task, worker_id=worker_id)
    try:
        return processor.process()
    except (ListNotFound, RowNotFound):
        # The row or list vanished between resolve and land (a user
        # deletion mid-run): terminal, nothing to diagnose. A fill-backed
        # run also nudges completion so the fill does not strand live on a
        # row that disappeared.
        flow.settle(task.id, status=FillTaskStatus.ROW_MISSING, result={})
        if task.fill_run_id:
            fill_progress.try_finish(task.fill_run_id)
        return "row_missing"


class FillTaskConsumer:
    """The consume loop. State lives in the bus + the task rows, never in
    this process, so a restart resumes from the committed offset. `topic`
    selects the lane (the AUTOFILL firehose or the manual fills); the
    handler routes each task on its own fill_run_id regardless."""

    def __init__(self, *, worker_id: str, stop, topic=AUTOFILL_TASKS) -> None:
        self.worker_id = worker_id
        self.stop = stop
        self.topic = topic

    def run(self, once: bool = False) -> None:
        from confluent_kafka import Consumer

        consumer = Consumer(
            {
                "bootstrap.servers": settings.INGEST_KAFKA_BOOTSTRAP_SERVERS,
                "group.id": self.topic.consumer_group,
                "auto.offset.reset": "earliest",
                # Commit AFTER the claim/run/settle, not on a timer: a
                # mid-run death redelivers, and the claim CAS makes the
                # reprocess safe (a settled task's redelivery is dropped).
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([self.topic.name])
        try:
            while not self.stop.is_set():
                _touch_heartbeat()  # loop is turning, whether or not a message came
                msg = consumer.poll(1.0)
                if msg is None:
                    if once:
                        break
                    continue
                if msg.error():
                    logger.error("consume_fill_tasks consume error: %s", msg.error())
                    continue
                try:
                    task_id = json.loads(msg.value())["task_id"]
                except (ValueError, KeyError) as e:
                    # An undecodable message would poison the partition
                    # forever if left uncommitted; skip it (a dead-letter
                    # topic is a follow-up).
                    logger.error("consume_fill_tasks: undecodable message skipped: %s", e)
                    consumer.commit(message=msg)
                    continue
                try:
                    handle_fill_task(task_id, self.worker_id)
                    consumer.commit(message=msg)
                except DatabaseError as e:
                    # Transient (a DB restart, a reset socket, a lock
                    # timeout): the one process draining the queue must not
                    # die over a bounce (compose sets no restart policy). Drop
                    # the broken connection so the next query reopens, and
                    # leave the offset uncommitted; a task left PROCESSING is
                    # recovered by the reclaim cron. Mirrors the provisioner's
                    # DatabaseError branch.
                    logger.warning("consume_fill_tasks hit a database error, recovering: %s", e)
                    connection.close()
                    if once:
                        return
                    continue
        finally:
            consumer.close()

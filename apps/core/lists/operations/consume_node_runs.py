"""The shared node-run CONSUMER: the loop that claims tasks off the bus
and hands each to its node's processor. The consume loop lives here
(mirroring the ingest consumer); the management command owns only
signals; how a run executes lives on the processor of its node's kind
(lists/processors). ONE consumer serves both lanes, told which topic to
read.

`handle_node_run` is the separable unit, testable without a broker: it
claims the task through the state machine (READY | QUEUED -> PROCESSING,
accepting READY so a message that outran the provisioner's mark still
runs), hands it to its node's processor, and translates a row/list
deletion mid-run into a terminal ROW_MISSING.

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

from ..constants import FILL_RETRY_BACKOFF_SECONDS, NodeRunStatus
from ..ingest.topics import AUTOFILL_RUNS
from ..models import Node
from ..processors import RunOutcome, processor_for
from ..services.lists import ListNotFound, RowNotFound
from ..services.node_runs import NodeRunFlow

logger = logging.getLogger(__name__)

# Liveness marker the consume loop refreshes every iteration; the compose
# healthcheck marks the consumer unhealthy when it goes stale, catching a
# WEDGED loop without false-alarming on an idle bus (an idle consumer
# still loops and touches this). A fixed path so the healthcheck can name
# it; keep the two in sync.
_HEARTBEAT_PATH = Path("/tmp/consume_node_runs.heartbeat")


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError as e:
        logger.warning("consume_node_runs heartbeat write failed: %s", e)


def handle_node_run(task_id: str, worker_id: str) -> RunOutcome | None:
    """Claim one task and hand it to its node's processor. Returns None
    when the claim CAS lost (a duplicate delivery, or the reclaim scan /
    another consumer got there first), else what the processor did with
    it. A database error PROPAGATES, so the offset is not committed and
    the message redelivers; any other crash inside the run parks the
    task (a retry with backoff) or, past the attempt cap, settles it
    DONE unrun, so one bad task never takes the consumer down."""
    flow = NodeRunFlow(worker_id=worker_id)
    task = flow.claim(task_id)
    if task is None:
        return None
    node = Node.objects.filter(id=task.node_id, account_id=task.account_id).first()
    if node is None:
        # Nodes die only with their list, so a run still pointing at one
        # is corruption; settle rather than crash-loop the consumer, but
        # say so (unlike the allowed agent orphaning, which the processor
        # settles silently).
        logger.warning("node run %s: node %s is gone; settling it unrun", task.id, task.node_id)
        flow.settle(task.id, status=NodeRunStatus.DONE, result={})
        return RunOutcome.DONE
    try:
        return processor_for(account_id=task.account_id, node=node).process_run(task, flow=flow)
    except (ListNotFound, RowNotFound):
        # The row or list vanished between resolve and land (a user
        # deletion mid-run): terminal, nothing to diagnose. A fill-backed
        # run's fill notices on its next poll of its runs.
        flow.settle(task.id, status=NodeRunStatus.ROW_MISSING, result={})
        return RunOutcome.ROW_MISSING
    except DatabaseError:
        # Transient: the loop's own branch recovers the connection and
        # leaves the offset uncommitted, so the message redelivers.
        raise
    except Exception:
        # Anything else is a crash inside the run (a bug, a node whose
        # config no longer parses, a kind the processor cannot run). The
        # one process draining the queue must not die over one task, nor
        # redeliver it forever: log the traceback, retry with the standard
        # backoff while attempts remain, and settle DONE with no result
        # once they are spent (the cell stays never-attempted; the failure
        # is the run's, not the row's, so nothing is diagnosed on a cell).
        logger.exception("node run %s crashed on attempt %d", task.id, task.attempts)
        if flow.exhausted(task):
            flow.settle(task.id, {}, status=NodeRunStatus.DONE)
            return RunOutcome.DONE
        flow.park(task.id, backoff_seconds=FILL_RETRY_BACKOFF_SECONDS * task.attempts, result={})
        return RunOutcome.PARKED


class NodeRunConsumer:
    """The consume loop. State lives in the bus + the task rows, never in
    this process, so a restart resumes from the committed offset. `topic`
    selects the lane (the AUTOFILL firehose or the manual fills); the
    handler routes each task on its own fill_run_id regardless."""

    def __init__(self, *, worker_id: str, stop, topic=AUTOFILL_RUNS) -> None:
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
                    logger.error("consume_node_runs consume error: %s", msg.error())
                    continue
                try:
                    task_id = json.loads(msg.value())["task_id"]
                except (ValueError, KeyError) as e:
                    # An undecodable message would poison the partition
                    # forever if left uncommitted; skip it (a dead-letter
                    # topic is a follow-up).
                    logger.error("consume_node_runs: undecodable message skipped: %s", e)
                    consumer.commit(message=msg)
                    continue
                try:
                    handle_node_run(task_id, self.worker_id)
                    consumer.commit(message=msg)
                except DatabaseError as e:
                    # Transient (a DB restart, a reset socket, a lock
                    # timeout): the one process draining the queue must not
                    # die over a bounce (compose sets no restart policy). Drop
                    # the broken connection so the next query reopens, and
                    # leave the offset uncommitted; a task left PROCESSING is
                    # recovered by the reclaim cron. Mirrors the provisioner's
                    # DatabaseError branch.
                    logger.warning("consume_node_runs hit a database error, recovering: %s", e)
                    connection.close()
                    if once:
                        return
                    continue
        finally:
            consumer.close()

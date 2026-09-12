"""The shared PROVISIONER loop: pick READY tasks, publish them to the bus,
mark QUEUED only after the ack. Two lanes subclass it and differ in ONE
thing, WHICH tasks a pass picks (the autofill firehose picks globally; the
manual lane walks live fills at a flat per-fill depth). Everything else,
the loop, the batched publish, the lazy producer, the heartbeat, lives
here. The process (signals, lifecycle) lives in the management command.

Publish FIRST, then mark QUEUED: a crash between the two leaves the task
READY, so the next pass re-picks and re-publishes it, and the consumer's
claim CAS dedups the double delivery (READY | QUEUED -> PROCESSING once).
The reverse (mark, then publish) could strand a QUEUED task no message
ever names.

Single-threaded and stateless: state is the task row and the bus offset,
so a restart re-derives everything from the tasks that survive.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError

from ...models import FillTask

logger = logging.getLogger(__name__)

# Block this long for the broker to ack a produced page before marking it
# QUEUED: mark_queued is only honest once the message is durable on the
# bus, so a slow or down broker must surface as a failed publish that
# leaves the tasks READY, not QUEUED tasks no consumer will ever see.
_PRODUCE_TIMEOUT_SECONDS = 10.0


class ProvisionPublishError(Exception):
    """A page could not be put on the bus, so its tasks stay READY and the
    next pass re-picks them. Raised rather than swallowed so the mark to
    QUEUED never runs for a page the broker did not ack."""


class ProvisionOperation:
    """The provisioning loop. One instance per process; the producer
    connection is lazy and reused across passes. A lane subclass sets its
    heartbeat path, idle interval, and name, and implements `_one_pass`."""

    _HEARTBEAT_PATH: Path
    _IDLE_SECONDS: int
    _LANE: str

    def __init__(self, *, worker_id: str, stop) -> None:
        self.worker_id = worker_id
        self.stop = stop
        self._producer = None  # lazy: constructed on first publish, then reused

    def _get_producer(self):
        if self._producer is None:
            from confluent_kafka import Producer

            self._producer = Producer({"bootstrap.servers": settings.INGEST_KAFKA_BOOTSTRAP_SERVERS})
        return self._producer

    def _touch_heartbeat(self) -> None:
        # Liveness marker the loop refreshes every pass; the compose
        # healthcheck marks the provisioner unhealthy when it goes stale,
        # catching a WEDGED loop without false-alarming on an empty queue
        # (an idle provisioner still loops and touches this). A fixed path
        # the healthcheck names; keep the two in sync.
        try:
            self._HEARTBEAT_PATH.touch()
        except OSError as e:
            # A failed heartbeat is itself the unhealthy signal (the
            # healthcheck fires); never crash the loop over it.
            logger.warning("%s provisioner heartbeat write failed: %s", self._LANE, e)

    def _publish_batch(self, tasks: list[FillTask], topic) -> None:
        """Produce every task id in the page to `topic` (buffered,
        non-blocking), keyed by row id (an even, query-free spread that
        keeps a row's tasks on one partition), then BLOCK ONCE for the
        whole page's acks. The single flush amortizes the broker round-trip
        across the page instead of paying it per task (librdkafka pipelines
        the produces). Raises ProvisionPublishError when the page does not
        fully ack, so the caller marks none of it QUEUED and the next pass
        re-publishes it (the consumer's claim CAS drops the duplicates)."""
        failures: list = []

        def _on_delivery(err, _msg) -> None:
            if err is not None:
                failures.append(err)

        producer = self._get_producer()
        for task in tasks:
            producer.produce(
                topic.name,
                key=task.row_id.encode(),
                value=json.dumps({"task_id": str(task.id)}).encode(),
                on_delivery=_on_delivery,
            )
        remaining = producer.flush(timeout=_PRODUCE_TIMEOUT_SECONDS)
        if remaining > 0:
            raise ProvisionPublishError(f"{self._LANE} publish timed out ({remaining} unacked)")
        if failures:
            raise ProvisionPublishError(f"{self._LANE} publish failed: {failures[0]}")

    def run(self, once: bool = False) -> None:
        while not self.stop.is_set():
            self._touch_heartbeat()  # the loop is turning, empty queue or not
            try:
                worked = self._one_pass()
            except DatabaseError as e:
                # Transient (a restart mid-connection, a lock timeout):
                # idle and retry rather than crash, like the workers.
                logger.warning("%s provisioner hit a database error, retrying: %s", self._LANE, e)
                if once:
                    return
                self.stop.wait(self._IDLE_SECONDS)
                continue
            except ProvisionPublishError as e:
                # A broker blip: back off and retry the pass so a transient
                # broker never crashes the provisioner. Tasks the failed
                # publish did not mark stay READY, so nothing is stranded.
                logger.warning("%s provisioner: publish failed, backing off: %s", self._LANE, e)
                if once:
                    return
                self.stop.wait(self._IDLE_SECONDS)
                continue
            if not worked:
                if once:
                    break
                self.stop.wait(self._IDLE_SECONDS)  # SIGTERM wakes it

    def _one_pass(self) -> bool:
        """One provisioning pass: pick READY tasks, publish, mark QUEUED.
        Returns whether anything was published, so the loop idles (or
        `--once` stops) on a quiet pass. The lane decides WHICH tasks."""
        raise NotImplementedError

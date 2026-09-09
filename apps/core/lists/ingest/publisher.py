"""The publish seam for accepted ingest events.

The webhook ACCEPTS a push and publishes it here for asynchronous append;
the endpoint never touches a broker directly, so the durable backend can
change without changing the endpoint. `get_ingest_publisher` is the single
swap point, selected by settings:

- INGEST_KAFKA_BOOTSTRAP_SERVERS set  -> KafkaIngestPublisher (the real bus).
- unset (a dev stack without Kafka, and every test) -> LoggingIngestPublisher,
  which LOGS AND DROPS. There is no durability on that path, so a pushed
  batch is acknowledged (202) but not retained; do not build anything that
  assumes an accepted event survives it.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from django.conf import settings

from .events import IngestEvent, to_wire
from .topics import LIST_ROWS_INGESTED, TopicSpec

logger = logging.getLogger(__name__)

# Block this long for the broker to ack a produced batch before the endpoint
# returns: the 202 is only honest once the event is on the bus, so a slow or
# down broker must surface as a failed publish, not a silent drop.
_PRODUCE_TIMEOUT_SECONDS = 10.0


class IngestPublisher(Protocol):
    def publish(self, event: IngestEvent) -> None: ...


class IngestPublishError(Exception):
    """The event could not be put on the bus, so the push was not accepted.
    The endpoint surfaces this as a 5xx and the caller retries (dedupe makes
    a retry safe), rather than a 202 that silently lost the batch."""


class LoggingIngestPublisher:
    """Interim publisher: records that an ingest arrived, then drops it. Used
    only where no bus is configured. NOT durable; see the module docstring."""

    def publish(self, event: IngestEvent) -> None:
        logger.info(
            "ingest accepted (no bus configured: NOT persisted) event=%s list=%s account=%s rows=%d",
            event.event_id,
            event.list_id,
            event.account_id,
            len(event.rows),
        )


class KafkaIngestPublisher:
    """Produces the accepted batch to its topic as one message, keyed by list
    id (so a list's pushes keep per-partition order), and BLOCKS for the
    broker ack so the 202 is only returned once the event is durable on the
    bus. A whole batch is one message; the producer's message.max.bytes is
    driven from the same INGEST_MAX_MESSAGE_BYTES as the topic and broker, so
    the three agree (see lists.ingest.topics)."""

    def __init__(self, bootstrap: str, spec: TopicSpec) -> None:
        self._bootstrap = bootstrap
        self._spec = spec
        self._producer = None  # lazy: constructed on first publish, then reused

    def _get_producer(self):
        if self._producer is None:
            from confluent_kafka import Producer

            self._producer = Producer(
                {
                    "bootstrap.servers": self._bootstrap,
                    "message.max.bytes": settings.INGEST_MAX_MESSAGE_BYTES,
                }
            )
        return self._producer

    def publish(self, event: IngestEvent) -> None:
        failures: list = []

        def _on_delivery(err, _msg) -> None:
            if err is not None:
                failures.append(err)

        producer = self._get_producer()
        producer.produce(
            self._spec.name,
            key=event.list_id.encode(),
            value=json.dumps(to_wire(event)).encode(),
            on_delivery=_on_delivery,
        )
        # flush returns the count still in the queue: >0 means the ack did not
        # arrive within the timeout, so the produce is not confirmed.
        remaining = producer.flush(timeout=_PRODUCE_TIMEOUT_SECONDS)
        if remaining > 0:
            raise IngestPublishError(f"ingest publish timed out ({remaining} unacked)")
        if failures:
            raise IngestPublishError(f"ingest publish failed: {failures[0]}")


# Cache the Kafka publisher per bootstrap so its producer connection is reused
# across requests; the Logging publisher is cheap and stateless, so it is made
# fresh (and never caches a would-be Kafka client under test).
_kafka_publishers: dict[str, KafkaIngestPublisher] = {}


def get_ingest_publisher() -> IngestPublisher:
    """The publisher seam. Returns the configured publisher for the row-push
    topic; the durable backend is selected by settings without touching the
    endpoint that calls it."""
    bootstrap = settings.INGEST_KAFKA_BOOTSTRAP_SERVERS
    if not bootstrap:
        return LoggingIngestPublisher()
    if bootstrap not in _kafka_publishers:
        _kafka_publishers[bootstrap] = KafkaIngestPublisher(bootstrap, LIST_ROWS_INGESTED)
    return _kafka_publishers[bootstrap]

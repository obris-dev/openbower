"""The publish seam for accepted ingest events.

The webhook ACCEPTS a push and publishes it here for asynchronous append;
the endpoint never touches a broker directly, so the durable backend can
change without changing the endpoint. `get_ingest_publisher` is the single
swap point: the durable backend replaces the interim publisher below behind
the same `IngestPublisher` protocol.

INTERIM: the default publisher LOGS AND DROPS the event. There is no
durability yet, so a pushed batch is acknowledged (202) but not retained.
Do not build anything that assumes an accepted event survives.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .events import IngestEvent

logger = logging.getLogger(__name__)


class IngestPublisher(Protocol):
    def publish(self, event: IngestEvent) -> None: ...


class LoggingIngestPublisher:
    """Interim publisher: records that an ingest arrived, then drops it.
    Replaced by a durable backend (outbox, then Kafka) behind this same
    protocol. NOT durable; see the module docstring."""

    def publish(self, event: IngestEvent) -> None:
        logger.info(
            "ingest accepted (interim: NOT persisted) event=%s list=%s account=%s rows=%d",
            event.event_id,
            event.list_id,
            event.account_id,
            len(event.rows),
        )


def get_ingest_publisher() -> IngestPublisher:
    """The publisher seam. Returns the configured ingest publisher; the
    durable backend swaps in here (selected by settings) without touching
    the endpoint that calls it.

    Constraint for that backend: a whole accepted batch must ride as ONE bus
    message (never split here), so the bus message limit must exceed the
    largest batch the endpoint accepts. That accept size is bounded today
    only by the row cap plus Django's DATA_UPLOAD_MAX_MEMORY_SIZE, until a
    byte cap is set. On Kafka the ceiling is FOUR settings that must agree or
    produce/consume fails silently: topic max.message.bytes, broker
    message.max.bytes, producer max.request.size, consumer
    max.partition.fetch.bytes."""
    return LoggingIngestPublisher()

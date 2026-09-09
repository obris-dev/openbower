"""The publish seam for accepted ingest events.

The webhook ACCEPTS a push and publishes it here for asynchronous append;
the endpoint never touches a broker directly, so the durable backend can
change without changing the endpoint. `get_ingest_publisher` is the single
swap point: an outbox worker, then a Kafka producer, replaces the interim
publisher below behind the same `IngestPublisher` protocol.

INTERIM: the default publisher LOGS AND DROPS the event. There is no
durability yet, so a pushed batch is acknowledged (202) but not retained.
This is deliberate scaffolding for the seam; the durable backend and the
append worker are follow-ups. Do not build anything that assumes an
accepted event survives.
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
    """The publisher seam. Returns the configured ingest publisher; swap the
    durable backend in here (selected by settings once one exists) without
    touching the endpoint that calls it.

    Sizing invariant for that durable backend: a whole accepted batch must
    ride as ONE bus message (no chunking here), so the bus message limit
    must exceed the ingest accept byte cap, with headroom. On Kafka that is
    FOUR settings that must agree or produce/consume fails silently: topic
    max.message.bytes, broker message.max.bytes, producer max.request.size,
    and consumer max.partition.fetch.bytes. Drive them from one value and
    keep it >= the accept cap; a batch is bounded at accept, never split
    here or rescued by catching an oversize error."""
    return LoggingIngestPublisher()

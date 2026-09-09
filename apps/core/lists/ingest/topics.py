"""The ingest topic catalog: each topic's config declared as data, so a new
topic is one entry here (plus a `provision_topics` run), not another scatter
of settings and inline client dicts. `provision_topics` reconciles the
broker to this catalog; the producer and worker read their topic name +
consumer group from it.

Sizing: a whole accepted batch rides as ONE message, so `max.message.bytes`
(from settings.INGEST_MAX_MESSAGE_BYTES) must exceed the endpoint's accept
bound, and the broker/producer/consumer limits must all agree with it (the
compose kafka env + the two client configs). Keyed by list id at produce
time, so a list's pushes keep per-partition order.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

# A week: a redelivery only happens for so long, and the dedupe inbox is the
# durable record, so the bus itself needs only a short retention.
_RETENTION_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class TopicSpec:
    name: str
    consumer_group: str
    partitions: int = 6
    # Single-broker dev; a multi-broker deploy raises this (a future env knob).
    replication_factor: int = 1
    retention_ms: int = _RETENTION_MS

    def kafka_config(self) -> dict[str, str]:
        """Topic-level overrides applied at provisioning. max.message.bytes is
        the bus half of the one-message-per-batch invariant."""
        return {
            "max.message.bytes": str(settings.INGEST_MAX_MESSAGE_BYTES),
            "retention.ms": str(self.retention_ms),
            "cleanup.policy": "delete",
        }


LIST_ROWS_INGESTED = TopicSpec(name="list.rows.ingested", consumer_group="ingest-append-worker")

# What provision_topics walks. A new topic appends an entry.
TOPICS: tuple[TopicSpec, ...] = (LIST_ROWS_INGESTED,)

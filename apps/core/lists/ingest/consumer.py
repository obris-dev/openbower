"""The consume half of the ingest bus, collocated with the publish half: a
worker consumes accepted row-push events, dedupes them against the inbox, and
appends the rows to the sheet.

The append goes through AppendRowsOperation, which starts the sheet's
workflow for the new rows in the same transaction, so a pushed row's AI
columns fill like any other arrival's. The loop lifecycle
(subscribe, poll, commit, stop) lives here; the management command owns only
signals. `handle_ingest_event` is separable and tested without a broker.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import NamedTuple

from django.conf import settings
from django.db import transaction

from ..models import ProcessedIngestEvent
from ..operations.append_rows import AppendRowsOperation
from ..services.lists import ColumnNotWritable, ListNotFound, ListService, ListsFull
from .events import IngestEvent, from_wire
from .topics import LIST_ROWS_INGESTED

logger = logging.getLogger(__name__)

# Liveness marker the consume loop refreshes every iteration; the compose
# healthcheck marks the worker unhealthy when it goes stale, which catches a
# WEDGED loop (deadlock, a blocked poll) without false-alarming on an idle bus
# (an idle consumer still loops and touches this). A fixed path so the
# healthcheck can name it directly; keep the two in sync. It proves the loop
# is turning, not that partitions are assigned and advancing (a deeper
# lag/assignment check is a future refinement).
_HEARTBEAT_PATH = Path("/tmp/ingest_worker.heartbeat")


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError as e:
        # A failed heartbeat is itself the unhealthy signal (the healthcheck
        # fires); never crash the consume loop over it.
        logger.warning("ingest heartbeat write failed: %s", e)


class AppendResult(NamedTuple):
    """The side effect's report back to the orchestrator. `applied` False is
    a terminal DROP (a deleted or full list, nothing a retry fixes), with
    its `reason`; True carries the `added` count. A TRANSIENT failure raises
    instead of returning, so the caller rolls back and redelivers."""

    applied: bool
    added: int = 0
    reason: str = ""


def _append_rows(event: IngestEvent) -> AppendResult:
    """The side effect: the pushed rows through the one append (rows
    and their workflow trigger, one transaction), owning its own
    terminal-error handling (a deleted or full list is a drop, reported
    for the caller to log). Only transient failures (a DatabaseError)
    raise. Rides the caller's transaction (handle_ingest_event's
    atomic), so a redelivery cannot double-apply."""
    lists = ListService(account_id=event.account_id)
    try:
        target = lists.get(event.list_id)
        report = AppendRowsOperation(account_id=event.account_id, target_list=target, rows=event.rows).run()
    except (ListNotFound, ListsFull, ColumnNotWritable) as e:
        # All three are TERMINAL for this event: a deleted or full list,
        # and a row carrying a column whose cells are recorded (the view
        # refuses that one at the door, so an event carrying it came
        # from somewhere that skipped the door and will never become
        # valid). Dropped and recorded, never retried, or the bus would
        # redeliver it forever.
        return AppendResult(applied=False, reason=str(e))
    return AppendResult(applied=True, added=report.added)


def handle_ingest_event(event: IngestEvent) -> str:
    """Process an event exactly once: dedupe on (account_id, event_id) and
    run the side effect, in ONE transaction so a redelivery cannot
    double-apply. Returns "applied", "skipped" (a redelivery), or "dropped"
    (a terminal failure the inbox row records, so it is not redelivered
    forever). A transient failure from the side effect propagates, rolling
    the whole unit back for a clean retry."""
    with transaction.atomic():
        _, created = ProcessedIngestEvent.objects.get_or_create(
            account_id=event.account_id,
            event_id=event.event_id,
            defaults={"list_id": event.list_id},
        )
        if not created:
            logger.info("ingest skip duplicate event=%s account=%s", event.event_id, event.account_id)
            return "skipped"
        result = _append_rows(event)
        if not result.applied:
            logger.warning("ingest dropped event=%s list=%s: %s", event.event_id, event.list_id, result.reason)
            return "dropped"
        logger.info(
            "ingest applied event=%s list=%s account=%s rows=%d",
            event.event_id,
            event.list_id,
            event.account_id,
            result.added,
        )
        return "applied"


class IngestConsumer:
    """The consume loop. State lives in the bus + the inbox, never in this
    process, so a restart resumes from the committed offset."""

    def __init__(self, *, worker_id: str, stop) -> None:
        self.worker_id = worker_id
        self.stop = stop

    def run(self, once: bool = False) -> None:
        from confluent_kafka import Consumer

        consumer = Consumer(
            {
                "bootstrap.servers": settings.INGEST_KAFKA_BOOTSTRAP_SERVERS,
                "group.id": LIST_ROWS_INGESTED.consumer_group,
                "auto.offset.reset": "earliest",
                # Commit AFTER the dedupe/apply transaction, not on a timer:
                # at-least-once delivery, and the inbox makes a reprocess safe.
                "enable.auto.commit": False,
                # The consumer half of the one-message-per-batch sizing (must
                # match the topic + producer + broker limits).
                "max.partition.fetch.bytes": settings.INGEST_MAX_MESSAGE_BYTES,
            }
        )
        consumer.subscribe([LIST_ROWS_INGESTED.name])
        try:
            while not self.stop.is_set():
                _touch_heartbeat()  # loop is turning, whether or not a message came
                msg = consumer.poll(1.0)
                if msg is None:
                    if once:
                        break
                    continue
                if msg.error():
                    logger.error("ingest consume error: %s", msg.error())
                    continue
                try:
                    event = from_wire(json.loads(msg.value()))
                except (ValueError, KeyError) as e:
                    # A message this worker cannot decode would poison the
                    # partition forever if left uncommitted; skip it (a
                    # dead-letter topic is a follow-up).
                    logger.error("ingest: undecodable message skipped: %s", e)
                    consumer.commit(message=msg)
                    continue
                handle_ingest_event(event)
                consumer.commit(message=msg)
        finally:
            consumer.close()

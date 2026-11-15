"""The ingest event: one accepted webhook push, typed at construction so
nothing downstream (the publisher, the worker) handles a bare dict.

Rows are IncomingRows, the shape every append takes; the worker appends
them through AppendRowsOperation, the one append every door takes, so
the row shape stays single.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings

from ..rows import ENVELOPE_CELLS, ENVELOPE_EXTERNAL_ID, IncomingRow, is_envelope


@dataclass(frozen=True)
class IngestEvent:
    # The idempotency key: the caller's own event id if they sent one, else
    # a ULID minted at accept time. Returned as the receipt and carried
    # through the bus so a re-delivery dedupes to one append and the rows can
    # be correlated back to this push (and, later, shown as pending in the
    # sheet). Deduped scoped by account: a caller-chosen key is not global.
    event_id: str
    sheet_id: str
    account_id: str
    user_id: str
    rows: list[IncomingRow]
    received_at: datetime


def to_wire(event: IngestEvent) -> dict:
    """The JSON-safe dict that rides the bus (received_at as ISO 8601). A
    hand-written codec, not a versioned schema type yet: promoting the event
    to a versioned wire type is a follow-up, so the consumer tolerates the
    shape it is handed rather than validating a version."""
    return {
        "event_id": event.event_id,
        "sheet_id": event.sheet_id,
        "account_id": event.account_id,
        "user_id": event.user_id,
        "rows": [_row_to_wire(row) for row in event.rows],
        "received_at": event.received_at.isoformat(),
    }


def from_wire(data: dict) -> IngestEvent:
    """Rebuild an IngestEvent from a bus message body."""
    return IngestEvent(
        event_id=data["event_id"],
        sheet_id=data["sheet_id"],
        account_id=data["account_id"],
        user_id=data["user_id"],
        rows=[_row_from_wire(row) for row in data["rows"]],
        received_at=datetime.fromisoformat(data["received_at"]),
    )


# Kafka sends a record's key and headers beside its value: the value keeps
# this much clear of the message cap.
_RECORD_HEADROOM_BYTES = 1024


class IngestTooLarge(Exception):
    """A push too big for one bus message (INGEST_MAX_MESSAGE_BYTES): it can
    never be accepted as sent, so it is refused, never retried."""

    def __init__(self, *, size: int, limit: int) -> None:
        super().__init__(f"this push is {size} bytes, over the {limit}-byte limit; send fewer rows per push")
        self.size = size
        self.limit = limit


def encode_event(event: IngestEvent) -> bytes:
    """The event as one bus message: its JSON in UTF-8, every character as
    sent (escaping non-ASCII would triple a body of accented text), so a
    message is about the size of the request that brought it. The request
    already refused text that is not valid Unicode (an unpaired
    surrogate), so it encodes. Raises IngestTooLarge."""
    text = json.dumps(to_wire(event), ensure_ascii=False)
    payload = text.encode()
    limit = settings.INGEST_MAX_MESSAGE_BYTES - _RECORD_HEADROOM_BYTES
    if len(payload) > limit:
        raise IngestTooLarge(size=len(payload), limit=limit)
    return payload


def _row_to_wire(row: IncomingRow) -> dict:
    # A row without an id rides as its bare cells, the shape a worker from
    # before ids reads, so a rolling deploy or a rollback still lands it.
    if not row.external_id:
        return row.cells
    return {ENVELOPE_CELLS: row.cells, ENVELOPE_EXTERNAL_ID: row.external_id}


def _row_from_wire(row: dict) -> IncomingRow:
    if not is_envelope(row):
        return IncomingRow(cells=row)
    return IncomingRow(cells=row[ENVELOPE_CELLS], external_id=row[ENVELOPE_EXTERNAL_ID])

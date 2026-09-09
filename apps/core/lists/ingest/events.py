"""The ingest event: one accepted webhook push, typed at construction so
nothing downstream (the publisher, the worker) handles a bare dict.

Rows are the same shape the manual-append path validates (a list of
str->str cell maps); the worker that eventually appends them reuses
ListService.add_rows, so the wire contract stays single.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IngestEvent:
    # The idempotency key: the caller's own event id if they sent one, else
    # a ULID minted at accept time. Returned as the receipt and carried
    # through the bus so a re-delivery dedupes to one append and the rows can
    # be correlated back to this push (and, later, shown as pending in the
    # sheet). Deduped scoped by account: a caller-chosen key is not global.
    event_id: str
    list_id: str
    account_id: str
    user_id: str
    rows: list[dict[str, str]]
    received_at: datetime


def to_wire(event: IngestEvent) -> dict:
    """The JSON-safe dict that rides the bus (received_at as ISO 8601). A
    hand-written codec, not a versioned schema type yet: promoting the event
    to a versioned wire type is a follow-up, so the consumer tolerates the
    shape it is handed rather than validating a version."""
    return {
        "event_id": event.event_id,
        "list_id": event.list_id,
        "account_id": event.account_id,
        "user_id": event.user_id,
        "rows": event.rows,
        "received_at": event.received_at.isoformat(),
    }


def from_wire(data: dict) -> IngestEvent:
    """Rebuild an IngestEvent from a bus message body."""
    return IngestEvent(
        event_id=data["event_id"],
        list_id=data["list_id"],
        account_id=data["account_id"],
        user_id=data["user_id"],
        rows=data["rows"],
        received_at=datetime.fromisoformat(data["received_at"]),
    )

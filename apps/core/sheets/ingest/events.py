"""The ingest event: one accepted webhook push, typed at construction so
nothing downstream (the publisher, the worker) handles a bare dict.

Rows are IncomingRows, the shape every append takes; the worker appends
them through AppendRowsOperation, the one append every door takes, so
the row shape stays single.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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

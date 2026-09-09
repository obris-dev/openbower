"""The ingest event: one accepted webhook push, typed at construction so
nothing downstream (the publisher, later a worker) handles a bare dict.

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
    # through the bus so a re-delivery dedupes to one append (once the
    # durable backend enforces it) and the rows can be correlated back to
    # this push (and, later, shown as pending in the sheet).
    event_id: str
    list_id: str
    account_id: str
    user_id: str
    rows: list[dict[str, str]]
    received_at: datetime

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
    # A ULID minted at accept time: returned to the caller as the receipt
    # and carried through the bus so the eventual rows can be correlated
    # back to this push (and, later, shown as pending in the sheet).
    event_id: str
    list_id: str
    account_id: str
    user_id: str
    rows: list[dict[str, str]]
    received_at: datetime

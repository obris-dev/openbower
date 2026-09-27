"""The digest payload, built from rows the sheet holds: pure functions
over a row, its cells, and its cell states, shared by the Send webhook
column's Test and, later, the scheduled flush. No ORM here: callers
read, this shapes."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime

from openbower_schema.fills import SETTLED_CELL_STATES
from openbower_schema.webhooks import WebhookDigestData, WebhookDigestItem, WebhookSheetRef

from ..constants import StoredCellState
from ..models import List

# A column is DONE for a row when it holds an answer or a blank with a
# reason, or, for a column that holds no value, when its send went out.
# A failure (a timeout, a missing tool, a model error) is neither: the
# receiver would get a blank that says nothing about the row, so a row
# carrying one never completes, and a barrier over its column stays
# shut for that row until the user re-asks it. A failed send is not
# done either: nothing reached the receiver, and a barrier behind it
# must not open. A blank with a reason is a real outcome for the
# receiver whichever prompt produced it.
DONE_CELL_STATES: frozenset[str] = frozenset({StoredCellState.FILLED, StoredCellState.SENT, *SETTLED_CELL_STATES})


def completion_of(records: Mapping[str, tuple[str, datetime]], wait_keys: list[str]) -> datetime | None:
    """When a row completed for a set of waited-on columns: the newest
    record time among them once EVERY one holds a done state, else None
    (absence means never attempted; a failure means not until the user
    re-asks the row)."""
    if any(key not in records or records[key][0] not in DONE_CELL_STATES for key in wait_keys):
        return None
    return max(records[key][1] for key in wait_keys)


EVENT_ID_HEX_LENGTH = 32


def event_id_of(*, scope: str, row_id: str, stamp: str, test: bool) -> str:
    """128 bits of SHA-256 over the composite: UUID-sized, so a receiver
    stores it like every other id it already keeps. The test flag is an
    input: a receiver that ignores test data must never find the live
    event's id already in its store."""
    lane = "test" if test else "live"
    digest = hashlib.sha256(f"{lane}:{scope}:{row_id}:{stamp}".encode()).hexdigest()
    return digest[:EVENT_ID_HEX_LENGTH]


def build_digest_item(
    *,
    scope: str,
    row_id: str,
    cells: dict[str, str],
    states: dict[str, str],
    completed_at: datetime | None,
    sent_at: datetime,
    test: bool,
) -> WebhookDigestItem:
    """One item. The event id is derived, not minted, so a redelivery
    rebuilt from the same cell state carries the same id with nothing
    stored: a hash over the lane (test or live), the scope, the row, and
    the time. The scope is what the digest is FOR (a sheet for a test
    send; the webhook node once one exists), so two webhook nodes of one
    sheet never share ids; the time is the completion, or the send time
    for a sample that has not completed. Hashed so the value is
    fixed-width and gives a receiver nothing to parse."""
    stamp = (completed_at or sent_at).isoformat()
    return WebhookDigestItem(
        event_id=event_id_of(scope=scope, row_id=row_id, stamp=stamp, test=test),
        row_id=row_id,
        completed_at=completed_at.isoformat() if completed_at else None,
        cells=cells,
        states=states,
    )


def build_digest_data(target_list: List, *, waited_on: list[str], items: list[WebhookDigestItem]) -> WebhookDigestData:
    return WebhookDigestData(
        sheet=WebhookSheetRef(id=str(target_list.id), label=target_list.label),
        waited_on=list(waited_on),
        items=items,
    )

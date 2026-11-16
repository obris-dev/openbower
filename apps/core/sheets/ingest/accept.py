"""Accepting a push: what every push endpoint does between parsing the
request and answering it. The rows are checked against the target and its
columns, then published to the ingest bus as one event naming where they
came from; the append happens later, off the bus. A sheet's own push
endpoint and a signal's webhook both go through here, so a push is
refused and accepted the same way whichever endpoint took it."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from openbower_kernel.fields import new_ulid
from openbower_schema.sheets import IngestAccepted

from ..constants import MAX_SHEET_ROWS, IngestErrorCode, RowSource
from ..models import Sheet
from ..operations.append_rows import require_appendable
from ..serializers import validate_ingest_rows
from .events import IngestEvent
from .publisher import IngestPublishError, get_ingest_publisher

logger = logging.getLogger(__name__)


class PushInvalid(Exception):
    """Rows that do not fit the sheet's columns, each problem named."""

    code = IngestErrorCode.INGEST_INVALID

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class PushTooBig(Exception):
    """More rows than the sheet has room for."""

    code = IngestErrorCode.SHEET_FULL

    def __init__(self, *, room: int, rows: int) -> None:
        super().__init__(
            f"this sheet has room for {room} more rows and the push carries {rows}; delete rows to make room"
        )
        self.room = room
        self.rows = rows


def accept_push(
    target_sheet: Sheet,
    rows: list[Any],
    *,
    request_id: str | None,
    account_id: str,
    user_id: str,
    source: RowSource,
) -> IngestAccepted:
    """The push validated against its sheet and on the bus. `rows` are the
    envelope's rows as its request validated them. Raises
    SheetNotAppendable, PushInvalid, PushTooBig, IngestTooLarge or IngestPublishError, each
    answered by its endpoint (sheets.ingest.refusals)."""
    # Refused before the bus: the append happens later, so the push is the
    # only place the producer can be answered.
    require_appendable(target_sheet, source=source)
    # Checked against the columns before acceptance: the append is async
    # (202), so this is the only place a malformed value can reach the
    # producer as something it can fix. The rows come back through the
    # writers' transform, so the bus carries exactly what the append stores.
    problems, typed = validate_ingest_rows(target_sheet, rows)
    if problems:
        raise PushInvalid(problems)
    # The append refuses a batch past the row cap whole, and off the bus no
    # one hears it, so a push that cannot fit is answered here. Advisory: the
    # append still decides, and a row whose external_id the sheet already
    # holds is counted though it would be skipped.
    room = max(MAX_SHEET_ROWS - target_sheet.row_count, 0)
    if len(typed) > room:
        raise PushTooBig(room=room, rows=len(typed))
    # The caller's request_id if they sent one, else a fresh ULID: the bus
    # event's id, so a retry or a redelivery appends once.
    event = IngestEvent(
        event_id=request_id or new_ulid(),
        sheet_id=str(target_sheet.id),
        account_id=account_id,
        user_id=user_id,
        rows=typed,
        received_at=timezone.now(),
        source=source,
    )
    publisher = get_ingest_publisher()
    try:
        publisher.publish(event)
    except IngestPublishError as e:
        logger.warning("ingest publish failed for sheet %s: %s", event.sheet_id, e)
        raise
    return IngestAccepted(request_id=event.event_id, accepted=len(typed))

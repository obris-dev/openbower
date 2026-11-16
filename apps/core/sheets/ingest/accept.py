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

from ..constants import IngestErrorCode, RowSource
from ..models import Sheet
from ..operations.append_rows import SheetNotAppendable, require_appendable
from ..serializers import IngestRequest, validate_ingest_rows
from .events import IngestEvent
from .publisher import IngestPublishError, get_ingest_publisher

logger = logging.getLogger(__name__)


class PushRefused(Exception):
    """A push answered without reaching the bus: its status and the
    {error, detail} body the producer reads."""

    def __init__(self, status: int, *, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.body = {"error": error, "detail": detail}


def accept_push(target_sheet: Sheet, data: Any, *, account_id: str, user_id: str, source: RowSource) -> IngestAccepted:
    """The push validated and on the bus, or PushRefused. Raises DRF's
    ValidationError for a body off the envelope."""
    serializer = IngestRequest(data=data)
    serializer.is_valid(raise_exception=True)
    # Refused before the bus: the append happens later, so the push is the
    # only place the producer can be answered with a 409.
    try:
        require_appendable(target_sheet, source=source)
    except SheetNotAppendable as e:
        raise PushRefused(409, error=e.code, detail=str(e)) from e
    # Checked against the columns before acceptance: the append is async
    # (202), so this is the only place a malformed value can reach the
    # producer as a 400 it can fix. The rows come back through the writers'
    # transform, so the bus carries exactly what the append will store.
    problems, rows = validate_ingest_rows(target_sheet, serializer.validated_data["rows"])
    if problems:
        raise PushRefused(400, error=IngestErrorCode.INGEST_INVALID, detail="; ".join(problems))
    # The caller's idempotency key if they sent one, else a fresh ULID:
    # carried through the bus, so a retry or a redelivery appends once.
    event = IngestEvent(
        event_id=serializer.validated_data.get("event_id") or new_ulid(),
        sheet_id=str(target_sheet.id),
        account_id=account_id,
        user_id=user_id,
        rows=rows,
        received_at=timezone.now(),
        source=source,
    )
    publisher = get_ingest_publisher()
    try:
        publisher.publish(event)
    except IngestPublishError as e:
        # Never reached the bus, so not accepted: a retryable 503, never a
        # 202 that dropped it. The event id makes the retry safe.
        logger.warning("ingest publish failed for sheet %s: %s", event.sheet_id, e)
        raise PushRefused(503, error=IngestErrorCode.INGEST_UNAVAILABLE, detail="ingest bus unavailable; retry") from e
    return IngestAccepted(event_id=event.event_id, accepted=len(rows))

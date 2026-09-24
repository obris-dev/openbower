"""A Send webhook column: holds no value. Its cell is its node's to
write (SENT, FAILED, at the send's landing), so a person's typing is
refused, which the base already says."""

from __future__ import annotations

from typing import ClassVar

from .base import ColumnKind
from .registry import register


class WebhookColumnKind(ColumnKind):
    KIND: ClassVar[str] = "webhook"
    # SENT or FAILED, written at the send's landing.
    RECORDS_CELL_STATE: ClassVar[bool] = True


register(WebhookColumnKind)

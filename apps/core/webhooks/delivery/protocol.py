"""The result of one delivery attempt, whoever sent it: the test button
today, the flush later, both recording the same shape."""

from __future__ import annotations

from dataclasses import dataclass

from ..constants import DeliveryStatus


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    http_status: int | None = None
    # A sentence for the user; empty on OK.
    error: str = ""
    duration_ms: int = 0
    # The head of a non-2xx answer, header values already masked.
    response_excerpt: str = ""

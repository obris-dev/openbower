"""The webhook kind: the node a Send webhook column IS, at rank 1 of
its own path behind a wait_until. Its config is where completed rows go
(a destination of this account), what rides (the payload column keys),
how often the flush batches them (one of the contract's cadence
presets, in seconds), and whether it is running. One per path, so its
identity is the path it sits on, bound when the path is written. The
flush's schedule state (a due cursor, backoff, the watermark) arrives
with the flush."""

from __future__ import annotations

from typing import ClassVar, Self

from ..constants import DEFAULT_WEBHOOK_CADENCE_SECONDS
from .base import NodeConfig
from .registry import register


class Webhook(NodeConfig):
    KIND: ClassVar[str] = "webhook"
    DISPLAY: ClassVar[str] = "Send webhook"
    destination_id: str
    payload_keys: list[str]
    interval_seconds: int = DEFAULT_WEBHOOK_CADENCE_SECONDS
    enabled: bool = True
    # Bound by the writer; blank only in memory before the path exists.
    path_id: str = ""

    def _identity(self) -> str:
        return self.path_id

    def bound_to_path(self, path_id: str) -> Self:
        return self.model_copy(update={"path_id": path_id})


register(Webhook)

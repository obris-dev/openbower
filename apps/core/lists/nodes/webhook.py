"""The webhook kind: the node a Send webhook column IS, behind a
wait_until on its own path. Its config is where completed rows go (a
destination of this account), what rides (the payload column keys),
how often the flush batches them (one of the contract's cadence
presets, in seconds), and whether it is running. No identity: nothing
looks a webhook node up by one, and a path may hold several at
successive ranks (the rank key keeps each slot unique). The flush's
schedule state (a due cursor, backoff, the watermark) arrives with the
flush."""

from __future__ import annotations

from typing import ClassVar

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

    def _identity(self) -> str:
        return ""


register(Webhook)

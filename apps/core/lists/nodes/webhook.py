"""The webhook kind: the node a Send webhook column IS, behind a
wait_until on its own path. Its config is where completed rows go (a
destination of this account), what rides (the payload column keys),
how often the flush batches them (one of the contract's cadence
presets, in seconds), and whether it is running. No identity: nothing
looks a webhook node up by one, and a path may hold several at
successive ranks (the rank key keeps each slot unique). The schedule
state lives on the node's RUNS (services/webhook_runs.py): each row's
run holds its window in `not_before` and the flush claims what is
due."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from ..constants import DEFAULT_WEBHOOK_CADENCE_SECONDS
from .base import NodeConfig
from .registry import WEBHOOK, register


class Webhook(NodeConfig):
    KIND: ClassVar[str] = WEBHOOK
    DISPLAY: ClassVar[str] = "Send webhook"
    destination_id: str
    payload_keys: list[str]
    interval_seconds: int = Field(default=DEFAULT_WEBHOOK_CADENCE_SECONDS, gt=0)
    enabled: bool = True

    def _identity(self) -> str:
        return ""


register(Webhook)

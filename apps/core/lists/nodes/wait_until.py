"""The wait_until kind: the fan-in barrier a webhook column's path
starts with. Its config names the PATHS it waits on (never nodes: a
node appended or reordered on an upstream path later still means
"after that path ends"), each of which ends in a column_agent node. One
per path, so its identity is the path it sits on, bound when the path
is written. Inert structure until the per-row engine lands; the flush
finds it as the path's rank 0 and resolves the columns that end the
paths it names."""

from __future__ import annotations

from typing import ClassVar, Self

from .base import NodeConfig
from .registry import register


class WaitUntil(NodeConfig):
    KIND: ClassVar[str] = "wait_until"
    DISPLAY: ClassVar[str] = "Wait until"
    inbound_path_ids: list[str]
    # Bound by the writer; blank only in memory before the path exists.
    path_id: str = ""

    def _identity(self) -> str:
        return self.path_id

    def bound_to_path(self, path_id: str) -> Self:
        return self.model_copy(update={"path_id": path_id})


register(WaitUntil)

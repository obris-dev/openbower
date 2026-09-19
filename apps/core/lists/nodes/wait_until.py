"""The wait_until kind: the fan-in barrier a webhook column's path
starts with. Its config names the PATHS it waits on (never nodes: a
node appended or reordered on an upstream path later still means
"after that path ends"), each of which ends in a column_agent node. No
identity: nothing looks a wait node up by one, it is the path's rank 0
(the rank key keeps that slot unique). Inert structure until the
per-row engine lands; the flush finds it as the path's rank 0 and
resolves the columns that end the paths it names."""

from __future__ import annotations

from typing import ClassVar

from .base import NodeConfig
from .registry import WAIT_UNTIL, register


class WaitUntil(NodeConfig):
    KIND: ClassVar[str] = WAIT_UNTIL
    DISPLAY: ClassVar[str] = "Wait until"
    inbound_path_ids: list[str]

    def _identity(self) -> str:
        return ""


register(WaitUntil)

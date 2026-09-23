"""The entry kind: the marker at the head of every path fed by nothing
(an agent column's). The other marker is wait_until, the head of a path
fed by the paths it names; every path's head is exactly one of the two,
so "which paths does an arrival start" is one indexed read of this kind
rather than a walk of the workflow's nodes. Stores nothing and declares
no identity: it is addressed by its path and rank, it sorts first, and
a move never puts a node ahead of it. A marker, not work: it has no
processor, and a reaction skips it and offers the node behind it."""

from __future__ import annotations

from typing import ClassVar

from .base import NodeConfig
from .registry import ENTRY, register


class Entry(NodeConfig):
    KIND: ClassVar[str] = ENTRY
    DISPLAY: ClassVar[str] = "Entry"

    def _identity(self) -> str:
        return ""


register(Entry)

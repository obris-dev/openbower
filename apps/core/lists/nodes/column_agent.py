"""The column_agent kind: a node that runs one agent over a row and
writes that agent's outputs to the sheet's columns. Its config names the
agent. The bench node is the same kind with a blank agent: the account's
one sheetless node that a TEST run's node_id points at (the drafted
config a test runs rides the fill's frozen snapshot, never this node)."""

from __future__ import annotations

from typing import ClassVar

from .base import NodeConfig
from .registry import COLUMN_AGENT, register


class ColumnAgent(NodeConfig):
    KIND: ClassVar[str] = COLUMN_AGENT
    DISPLAY: ClassVar[str] = "Agent column"
    # Blank only on the bench node.
    agent_id: str = ""

    def _identity(self) -> str:
        return self.agent_id


register(ColumnAgent)

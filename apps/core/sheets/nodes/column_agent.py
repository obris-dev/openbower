"""The column_agent kind: a node that runs one agent over a row and
writes that agent's outputs to the sheet's columns. Its config names the
agent. The preview node is the same kind with a blank agent: the account's
one sheetless node that a preview run's node_id points at (the drafted
config a preview run executes rides the run's own input, never this
node)."""

from __future__ import annotations

from typing import ClassVar

from .base import NodeConfig
from .registry import COLUMN_AGENT, register

# The preview node's identity: a fixed word, since it has no agent and a
# blank identity would leave the account's one preview node out of the
# get-or-create key.
PREVIEW_IDENTITY = "preview"


class ColumnAgent(NodeConfig):
    KIND: ClassVar[str] = COLUMN_AGENT
    DISPLAY: ClassVar[str] = "Agent column"
    # Blank only on the preview node.
    agent_id: str = ""

    def _identity(self) -> str:
        if self.agent_id == "":
            return PREVIEW_IDENTITY
        return self.agent_id


register(ColumnAgent)

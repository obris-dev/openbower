"""What EVERY admission kind executes: the account/user custody and
the add-time model gate. Strictly the
shared kernel: a member lives here only while both kinds call it,
so reading this file IS reading what the kinds have in common (no
hooks, no overridables, nothing in-case)."""

from __future__ import annotations

from agents.providers import ModelUnavailable, model_for
from agents.tools import registry as tool_registry
from agents.tools.registry import UnknownTool
from openbower_schema.agents import AgentConfig

from .errors import ModelUnrunnable


class AdmissionBase:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    @staticmethod
    def _check_model(config: AgentConfig) -> None:
        """Add-time UX only; the worker's claim-time resolution is
        authoritative (a source can die mid-fill either way). The
        toggles resolve here too: a toggle naming no registered tool
        fails every row identically, so it refuses at add time."""
        try:
            model_for(config.provider, config.source, config.model)
            tool_registry.toggled_tools(config)
        except (ModelUnavailable, UnknownTool) as e:
            raise ModelUnrunnable(str(e)) from e

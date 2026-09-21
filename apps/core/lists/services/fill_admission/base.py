"""What admission executes before it decides anything: the
account/user custody and the add-time model gate. Strictly the shared
kernel of the admission paths (add and refill), so reading this file
IS reading what they have in common (no hooks, no overridables,
nothing in-case)."""

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

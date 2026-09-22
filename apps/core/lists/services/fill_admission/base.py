"""What admission executes before it decides anything: the
account/user custody and the add-time model gate. Strictly the shared
kernel of the admission paths (add and refill), so reading this file
IS reading what they have in common (no hooks, no overridables,
nothing in-case)."""

from __future__ import annotations

from openbower_schema.agents import AgentConfig

from ..runnable import ConfigUnrunnable, check_runnable
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
            check_runnable(config)
        except ConfigUnrunnable as e:
            raise ModelUnrunnable(str(e)) from e

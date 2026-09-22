"""The config-tier gate, ONE spelling: whether a drafted or stored
AgentConfig can run at all (its model resolves on this deploy, every
tool it toggles is registered). Admission refuses at the click, the
preview refuses at its start, and the worker treats the same pair of
errors at claim time as the fact that fails every row identically, so
the three never disagree about what config-tier means."""

from __future__ import annotations

from agents.providers import ModelUnavailable, model_for
from agents.tools import registry as tool_registry
from agents.tools.registry import UnknownTool
from openbower_schema.agents import AgentConfig

# The errors that mean "this config cannot run", wherever they surface.
CONFIG_TIER_ERRORS: tuple[type[Exception], ...] = (ModelUnavailable, UnknownTool)


class ConfigUnrunnable(Exception):
    """The config cannot run: the message names the model or the tool."""


def check_runnable(config: AgentConfig) -> None:
    """Resolve the model and the toggled tools, raising ConfigUnrunnable
    with the cause. UX at the click; the worker's claim-time resolution
    stays authoritative (a source can die mid-fill either way)."""
    try:
        model_for(config.provider, config.source, config.model)
        tool_registry.toggled_tools(config)
    except CONFIG_TIER_ERRORS as e:
        raise ConfigUnrunnable(str(e)) from e

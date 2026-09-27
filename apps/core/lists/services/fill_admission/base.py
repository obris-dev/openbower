"""The model gate the AI column create and a fill share before either
decides anything."""

from __future__ import annotations

from openbower_schema.agents import AgentConfig

from ..runnable import ConfigUnrunnable, check_runnable
from .errors import ModelUnrunnable


def check_model(config: AgentConfig) -> None:
    """Add-time UX only; the worker's claim-time resolution is
    authoritative (a source can die mid-fill either way). The toggles
    resolve here too: a toggle naming no registered tool fails every row
    identically, so it refuses up front. Run by the AI column's create
    (a broken config never becomes a column) and by every fill (the
    config may have changed since); the source's model roster is cached,
    so the second run in a row costs nothing."""
    try:
        check_runnable(config)
    except ConfigUnrunnable as e:
        raise ModelUnrunnable(str(e)) from e

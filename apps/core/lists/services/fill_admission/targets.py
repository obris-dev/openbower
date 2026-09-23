"""The free provider's row cap: how many rows a fill may consent to
before the free search vendor's budget refuses it. Which rows a fill
targets is the agent processor's judgement (lists/processors/
column_agent.py); this module keeps only the metering fact."""

from __future__ import annotations

from agents.tools.search.web_search import web_search_is_metered
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig

from ...constants import FREE_SEARCH_FILL_BUDGET, MAX_LIST_ROWS


def search_provider_is_free() -> bool:
    """Whether fill web searches run through a FREE vendor (which is
    what the budget bounds): the routing fact is the web-search
    wiring, read through the tool's own accessor, never the metered
    credentials (contact search runs its own metered roster
    regardless, and credentials alone route nothing)."""
    return not web_search_is_metered()


def free_provider_row_cap(config: AgentConfig) -> int:
    """How many rows this fill may consent to before the FREE search
    provider's budget refuses it. Gated on WEB search alone: the
    budget bounds the free scraping vendor, and contact search is
    metered (its own roster carries no free vendor) whatever the
    wiring says, so a contacts-only fill spends nothing free.
    Unbounded when the vendor is metered or no free tool runs."""
    if config.searches_web and search_provider_is_free():
        return FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS
    return MAX_LIST_ROWS

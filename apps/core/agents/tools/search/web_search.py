"""General web search, through the vendor the deployment WIRES to it:
the free keyless default, or a metered one when the operator picked
it. Which vendor serves (and so whether the remedy is connecting a
metered one) is a setting read at call time, never frozen at import.

This module also owns the deploy-level facts ABOUT web search that
other apps read (is it metered, which vendor is serving): the roster
and the wiring resolution live here, so a consumer never re-derives
them against a second copy of the facts."""

from __future__ import annotations

from pydantic_ai import RunContext

from ...constants import SearchStatus
from ...runtime.deps import CellDeps
from ..base import NOTE_EMPTY_QUERY, clamp_query, result_json
from ..registry import register
from .errors import SEARCH_ERRORS
from .machinery import (
    SearchToolSpec,
    provider_failure_copy,
    run_search,
    serving_display,
    serving_provider,
)
from .providers.registry import get, provider_status

# The vendors that may serve web search, the keyless default first.
VENDORS = ("duckduckgo", "serper")


def web_search(ctx: RunContext[CellDeps], query: str) -> str:
    """Search the web. Returns JSON: {"records": [{"record": N,
    "tool": "web", "title", "url", "snippet"}]}, exactly as the search
    provider returned them."""
    query = clamp_query(query.strip(), name=SPEC.name)
    if not query:
        return result_json([], NOTE_EMPTY_QUERY)
    return run_search(ctx.deps, query, spec=SPEC)


def web_search_is_metered() -> bool:
    """Whether THIS deploy's web searches spend money per query: the
    serving vendor is registered and metered. An unregistered or
    unwired vendor reads as unmetered, the cautious direction (the
    free-search fill budget then still caps the fill)."""
    try:
        return get(serving_provider(web_search.__name__, VENDORS)).metered
    except KeyError:
        return False


def open_vendor() -> str | None:
    """The vendor serving web search when it is ready to serve, else
    None: the catalog's search_provider slot, so the web's copy only
    ever names a vendor whose searches can actually run."""
    name = serving_provider(web_search.__name__, VENDORS)
    return name if provider_status(name) is SearchStatus.OPEN else None


# Names the vendor that ACTUALLY served this fill's searches, and
# offers the metered door as the remedy only when the free one failed
# (a metered vendor failing has no cheaper next step to offer).
# Callables because which vendor serves is a setting read at failure
# time.
_failure_copy = provider_failure_copy(
    "Web search",
    lambda: (
        "the free search provider" if not web_search_is_metered() else serving_display(web_search.__name__, VENDORS)
    ),
    lambda: (
        " Switch search to a metered vendor (a deployment setting) for metered search."
        if not web_search_is_metered()
        else ""
    ),
)


SPEC = SearchToolSpec(
    function=web_search,
    errors=SEARCH_ERRORS,
    # Declared, not derived: the family default names the serving
    # vendor plainly, and this tool's copy carries the free-vs-metered
    # story and the metered-door remedy.
    failure_copy=_failure_copy,
    # First in the blame walk: the general search is the likelier
    # culprit for an unserved blank than the scoped contacts tool.
    blame_order=1,
    record_label="web",
    vendors=VENDORS,
)

register(SPEC)

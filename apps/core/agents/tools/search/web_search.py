"""General web search, through the deployment's CONFIGURED search
provider: the free keyless default, or the paid one when the operator
picked it. Which provider serves (and so whether the remedy is
connecting the paid one) is a setting read at call time, never frozen
at import."""

from __future__ import annotations

from django.conf import settings
from pydantic_ai import RunContext

from ...constants import SearchProvider
from ...runtime.deps import CellDeps
from ..base import NOTE_EMPTY_QUERY, clamp_query, result_json
from ..registry import register
from .errors import SEARCH_ERRORS
from .machinery import SearchToolSpec, provider_availability, provider_failure_copy, run_search


def web_search(ctx: RunContext[CellDeps], query: str) -> str:
    """Search the web. Returns JSON: {"records": [{"record": N,
    "tool": "web", "title", "url", "snippet"}]}, exactly as the search
    provider returned them."""
    query = clamp_query(query.strip(), name=SPEC.name)
    if not query:
        return result_json([], NOTE_EMPTY_QUERY)
    return run_search(ctx.deps, query, spec=SPEC)


def _free() -> bool:
    return settings.SEARCH_PROVIDER != SearchProvider.DATAFORSEO


# Names the provider that ACTUALLY served this fill's searches, and
# offers the paid provider as the remedy only when the free one failed
# (the paid one failing has no cheaper next step to offer). Callables
# because which provider serves is a setting read at failure time.
_failure_copy = provider_failure_copy(
    "Web search",
    lambda: "the free search provider" if _free() else "DataForSEO",
    lambda: " Switch search to DataForSEO (a deployment setting) for metered search." if _free() else "",
)


SPEC = SearchToolSpec(
    function=web_search,
    # No pin: the family default, the deploy's configured switch,
    # serves both availability and every call.
    availability=provider_availability(),
    errors=SEARCH_ERRORS,
    failure_copy=_failure_copy,
    # First in the blame walk: the general search is the likelier
    # culprit for an unserved blank than the pinned contacts tool.
    blame_order=1,
    record_label="web",
)

register(SPEC)

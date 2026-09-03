"""The people x-ray: profile-page search PINNED to the paid provider
(the LinkedIn x-rays need Google-grade SERPs), whatever provider the
operator configured for the rest. The tool owns the scope in BOTH
directions: any model-supplied site: operator is stripped and the
people site injected, so the model chooses the query, never where it
runs; and a returned hit outside that site is dropped, because the
tool demanded the scope and an off-scope page provably cannot answer
(an engine that runs dry on a strict query relaxes it and serves
whatever it has, dressed as an answer)."""

from __future__ import annotations

import re

from pydantic_ai import RunContext

from ...constants import DEFAULT_PEOPLE_SITE, SearchProvider
from ...runtime.deps import CellDeps
from ..base import NOTE_EMPTY_QUERY, clamp_query, result_json
from ..registry import register
from .errors import SEARCH_ERRORS
from .machinery import SearchToolSpec, provider_availability, provider_failure_copy, run_search, scope_test

# Why the pool gained nothing when the provider DID serve: everything
# it served was off-scope. Distinct from an honest zero-hit drought
# (no note), and from a failure (the family's raises); like every note
# it leaves the next query the model's own decision.
NOTE_OFF_SCOPE = "the results were not profile pages and were discarded; none are worth reading for this query"


def find_contacts(ctx: RunContext[CellDeps], query: str) -> str:
    """Find people's public profile pages matching the query (role,
    company, location terms; no site: operator needed). Returns JSON:
    {"records": [{"record": N, "tool": "contacts", "title", "url",
    "snippet"}]}, profile pages only."""
    query = re.sub(r"(?i)\bsite\s*:\s*\S+\s*", "", query).strip()
    if not query:
        return result_json([], NOTE_EMPTY_QUERY)
    query = clamp_query(f"site:{DEFAULT_PEOPLE_SITE} {query}", name=SPEC.name)
    return run_search(ctx.deps, query, spec=SPEC)


SPEC = SearchToolSpec(
    function=find_contacts,
    availability=provider_availability(SearchProvider.DATAFORSEO),
    errors=SEARCH_ERRORS,
    # The pinned provider has no cheaper next step to offer, so the
    # remedy is empty whatever the configured one is.
    failure_copy=provider_failure_copy("Finding contacts", lambda: "DataForSEO", lambda: ""),
    record_label="contacts",
    display_name="Finding contacts",
    provider=SearchProvider.DATAFORSEO,
    # The check derives from the SAME pattern the query splices in,
    # so the demanded scope is one fact.
    check_hit=scope_test(DEFAULT_PEOPLE_SITE),
    rejected_note=NOTE_OFF_SCOPE,
)

register(SPEC)

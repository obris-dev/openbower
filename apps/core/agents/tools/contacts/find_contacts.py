"""The people x-ray: profile-page search over its OWN vendor roster
(the LinkedIn x-rays need real Google SERPs, so the free engine is
not on it), whatever vendor the operator wired for the rest. The
tool owns the scope in BOTH
directions: any model-supplied site: operator is stripped and the
people site injected, so the model chooses the query, never where it
runs; and a returned hit outside that site is dropped, because the
tool demanded the scope and an off-scope page provably cannot answer
(an engine that runs dry on a strict query relaxes it and serves
whatever it has, dressed as an answer)."""

from __future__ import annotations

import re

from pydantic_ai import RunContext

from ...constants import DEFAULT_PEOPLE_SITE
from ...runtime.deps import CellDeps
from ..base import NOTE_EMPTY_QUERY, clamp_query, result_json
from ..registry import register
from ..search.errors import SEARCH_ERRORS
from ..search.machinery import SearchToolSpec, run_search, scope_test

# The vendors whose SERPs are known to serve profile x-rays, the
# default first; the free engine is deliberately absent (it relaxes
# scoped queries into off-scope noise).
VENDORS = ("serper",)

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
    errors=SEARCH_ERRORS,
    blame_order=2,
    record_label="contacts",
    display_name="Finding contacts",
    vendors=VENDORS,
    # The check derives from the SAME pattern the query splices in,
    # so the demanded scope is one fact.
    check_hit=scope_test(DEFAULT_PEOPLE_SITE),
    rejected_note=NOTE_OFF_SCOPE,
)

register(SPEC)

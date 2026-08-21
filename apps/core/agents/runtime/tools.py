"""The NATIVE tools the model drives, on the framework's DEPENDENCY
CHANNEL: per-run state (the evidence pool, URL dedupe, and query
diagnoses) rides a CellDeps carried by RunContext, so every write is
visible in a signature, never a captured side effect. `build_tools` is
the factory deciding which tools the model is offered (toggles AND
door availability). The model decides when to call and with what
query; the SCOPE is never its to control: find_contacts strips any
model-supplied site: operator, injects the people site, and pins the
DataForSEO door (the requirement is about where those queries run, not
which door the operator picked for the rest)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic_ai import RunContext, Tool

from openbower_schema.agents import AgentConfig

from ..constants import DEFAULT_PEOPLE_SITE, EVIDENCE_MAX_LINES, QUERY_MAX_LENGTH, SearchProvider
from ..search import SearchOutcome, contacts_available, search, search_available
from .grounding import canonical_url


@dataclass
class CellDeps:
    """One cell walk's shared state: `evidence` is what grounding
    fences the answer to (together with URLs in the RENDERED prompt:
    row-fed URLs are the user's own ground truth), `outcomes` what the
    bench renders as per-query diagnoses. The tools fill evidence and
    outcomes DURING the framework run; the output validator and
    run_cell read them after."""

    prompt: str = ""
    evidence: list[str] = field(default_factory=list)
    # Structured hit URLs for the grounding pool (never re-parsed from
    # the display-formatted evidence lines).
    urls: list[str] = field(default_factory=list)
    outcomes: list[SearchOutcome] = field(default_factory=list)
    # Parallel tool calls can interleave seen's check-then-add; the
    # cost is a duplicated evidence line, never a wrong answer, so no
    # lock guards it.
    seen: set[str] = field(default_factory=set)


def web_search(ctx: RunContext[CellDeps], query: str) -> str:
    """Search the web. Returns result lines as 'title :: snippet [url]'."""
    query = query.strip()[:QUERY_MAX_LENGTH]
    if not query:
        return "no results (empty query)"
    if _already_searched(ctx.deps, query):
        return "already searched that exact query; answer from the evidence above"
    return _pool(ctx.deps, search(query))


def find_contacts(ctx: RunContext[CellDeps], query: str) -> str:
    """Find people's public profile pages matching the query (role,
    company, location terms; no site: operator needed). Returns result
    lines as 'title :: snippet [url]'."""
    query = re.sub(r"(?i)\bsite\s*:\s*\S+\s*", "", query).strip()
    if not query:
        return "no results (empty query)"
    query = f"site:{DEFAULT_PEOPLE_SITE} {query}"[:QUERY_MAX_LENGTH]
    if _already_searched(ctx.deps, query):
        return "already searched that exact query; answer from the evidence above"
    return _pool(ctx.deps, search(query, provider=SearchProvider.DATAFORSEO))


def build_tools(config: AgentConfig) -> list[Tool]:
    """What THIS config on THIS deploy may call: a toggled tool whose
    search door is closed is simply not offered."""
    tools: list[Tool] = []
    if config.searches_web and search_available():
        tools.append(Tool(web_search))
    if config.finds_contacts and contacts_available():
        tools.append(Tool(find_contacts))
    return tools


def _already_searched(deps: CellDeps, query: str) -> bool:
    """A repeated exact query is loop behavior, not new intent: answer
    from what it already returned instead of re-spending the metered
    call (the budget stays for QUERIES, not repeats)."""
    return any(outcome.query == query for outcome in deps.outcomes)


def _pool(deps: CellDeps, outcome: SearchOutcome) -> str:
    deps.outcomes.append(outcome)
    if outcome.failed:
        return "search failed (provider error); answer from evidence already gathered"
    lines = []
    for hit in outcome.hits:
        # Canonical dedupe: www./regional variants of one page pool
        # once (grounding compares canonically too).
        key = canonical_url(hit.url)
        if key in deps.seen:
            continue
        if len(deps.evidence) >= EVIDENCE_MAX_LINES:
            # A NAMED ceiling instead of an implicit one: the pool is
            # what every completion re-reads, so its size is a cost.
            break
        deps.seen.add(key)
        deps.urls.append(hit.url)
        line = f"{hit.title} :: {hit.snippet} [{hit.url}]"
        deps.evidence.append(line)
        lines.append(line)
    return "\n".join(lines) or "no results"

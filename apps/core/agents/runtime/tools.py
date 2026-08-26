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

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from pydantic_ai import RunContext, Tool

from openbower_schema.agents import AgentConfig

from ..constants import (
    DEFAULT_PEOPLE_SITE,
    EVIDENCE_MAX_LINES,
    QUERY_MAX_LENGTH,
    SearchProvider,
)
from ..search import SearchOutcome, contacts_available, search, search_available
from .grounding import canonical_url


def _live_web_search(query: str) -> SearchOutcome:
    return search(query)


def _live_contacts_search(query: str) -> SearchOutcome:
    return search(query, provider=SearchProvider.DATAFORSEO)


class EvidenceRecord(NamedTuple):
    """One pooled hit. `position` is 1-based and dense ACROSS THE WHOLE
    CELL RUN, not per search, so the numbered lines the model reads,
    the evidence stored on the outcome row, and the row drawer all
    speak one key. Display and ordering only: nothing cites it, since
    a citation the model types is a free-text integer that can point
    anywhere, and checking it deeper means matching against the
    answer, which is judgment.

    `tool` is which tool fetched the hit, so a reader can tell a
    general web result from a people result. NOT "door", which is this
    repo's word for a PROVIDER entry point; these are the runtime's
    own tools."""

    position: int
    tool: str
    title: str
    url: str
    snippet: str

    @property
    def text(self) -> str:
        return f"{self.title} :: {self.snippet}"

    def as_json(self) -> dict:
        """The MODEL's view of one record. JSON, so a snippet's own
        newlines and any "Record 7:" it happens to contain are DATA
        inside a string value, never structure the model could read as
        another record. That is what lets the provider's text through
        exactly as it was returned: nothing flattened, nothing cut."""
        return {
            "record": self.position,
            "tool": self.tool,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }

    @property
    def line(self) -> str:
        """The STORED view, for the outcome row and the row drawer.
        Never sent to the model, so it may carry the text as-is."""
        return f"Record {self.position} [{self.tool}] {self.title} :: {self.snippet} [{self.url}]"


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
    # The same pool STRUCTURED and numbered for the drawer
    # (evidence[i] is records[i].line; one pool, two views).
    records: list[EvidenceRecord] = field(default_factory=list)
    # Structured hit URLs for the grounding pool (never re-parsed from
    # the display-formatted evidence lines).
    urls: list[str] = field(default_factory=list)
    # key -> each surviving cell's confidence and the reason given for
    # it, written by the output validator; the worker persists them
    # with the row.
    assessments: dict = field(default_factory=dict)
    # Whether verification dropped any answered field: an all-blank row
    # with drops diagnoses UNVERIFIED, never a bare no-evidence.
    verification_dropped: bool = False
    outcomes: list[SearchOutcome] = field(default_factory=list)
    # Parallel tool calls can interleave seen's check-then-add; the
    # cost is a duplicated evidence line, never a wrong answer, so no
    # lock guards it.
    seen: set[str] = field(default_factory=set)
    # WHY a blank row is blank (a StoredCellState value, "" while unset):
    # the answerer and the doctrine guards write it, the fill worker
    # reads it (transient means retry, the rest are terminal causes).
    # The bench ignores it; its searches diagnosis already tells.
    blank_cause: str = ""
    # The search DOOR rides the dependency channel like every per-run
    # fact; the fill worker wraps these to time the search share of a
    # row without the runtime knowing it is being measured.
    web_search_fn: Callable[[str], SearchOutcome] = _live_web_search
    contacts_search_fn: Callable[[str], SearchOutcome] = _live_contacts_search


def web_search(ctx: RunContext[CellDeps], query: str) -> str:
    """Search the web. Returns JSON: {"records": [{"record": N,
    "tool": "web", "title", "url", "snippet"}]}, exactly as the search
    provider returned them."""
    query = query.strip()[:QUERY_MAX_LENGTH]
    if not query:
        return _result([], "empty query")
    if _already_searched(ctx.deps, query):
        return _result([], "already searched that exact query; answer from the records already gathered")
    return _pool(ctx.deps, ctx.deps.web_search_fn(query), tool="web")


def find_contacts(ctx: RunContext[CellDeps], query: str) -> str:
    """Find people's public profile pages matching the query (role,
    company, location terms; no site: operator needed). Returns JSON:
    {"records": [{"record": N, "tool": "contacts", "title", "url",
    "snippet"}]}, exactly as the search provider returned them."""
    query = re.sub(r"(?i)\bsite\s*:\s*\S+\s*", "", query).strip()
    if not query:
        return _result([], "empty query")
    query = f"site:{DEFAULT_PEOPLE_SITE} {query}"[:QUERY_MAX_LENGTH]
    if _already_searched(ctx.deps, query):
        return _result([], "already searched that exact query; answer from the records already gathered")
    return _pool(ctx.deps, ctx.deps.contacts_search_fn(query), tool="contacts")


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


def _result(records: list[dict], note: str = "") -> str:
    """ONE shape for every tool return, so the model never has to tell
    a sentence from a payload."""
    return json.dumps({"records": records, "note": note} if note else {"records": records}, ensure_ascii=False)


def _pool(deps: CellDeps, outcome: SearchOutcome, *, tool: str) -> str:
    deps.outcomes.append(outcome)
    if outcome.failed:
        return _result([], "search failed (provider error); answer from the records already gathered")
    pooled = []
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
        # Numbered ACROSS calls: the number is the citation key the
        # model, the validator, and the stored evidence all share.
        record = EvidenceRecord(
            position=len(deps.records) + 1,
            tool=tool,
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
        )
        deps.records.append(record)
        deps.evidence.append(record.line)
        pooled.append(record.as_json())
    return _result(pooled)

"""The NATIVE tools the model drives, on the framework's DEPENDENCY
CHANNEL: per-run state (the evidence pool, URL dedupe, each tool's
door status, and every call's outcome) rides a CellDeps carried by
RunContext, so every write is visible in a signature, never a captured
side effect. `build_tools` is the factory deciding which tools the
model is offered (toggles AND door status). The model decides when to
call and with what query; the SCOPE is never its to control:
find_contacts strips any model-supplied site: operator, injects the
people site, and pins the DataForSEO door (the requirement is about
where those queries run, not which door the operator picked for the
rest).

A door's status is PER TOOL within the run: web_search refusing never
refuses find_contacts, and never discards an answer the other tool
grounded. What each door said rides out of the run as data (the
outcomes and the final status per tool) for the fill operation to
record on the task and the cell."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import NamedTuple

from django.conf import settings
from pydantic_ai import RunContext, Tool

from openbower_schema.agents import AgentConfig

from ..constants import (
    DEFAULT_PEOPLE_SITE,
    EVIDENCE_MAX_LINES,
    QUERY_MAX_LENGTH,
    SEARCH_DOOR_CLOSERS,
    AgentTool,
    SearchProvider,
    SearchStatus,
)
from ..search import SearchHit, door_status, search
from .grounding import canonical_url
from .judgement import AnswerJudgement
from .outcomes import SearchOutcome

logger = logging.getLogger(__name__)

# The tool notes are ONE small vocabulary, so the model never reads
# prose that varies. Each one says what the records list cannot: why
# it is empty, and what to do about it. Only a closed door tells the
# model to stop calling THAT tool; the others say the pool is what it
# has for THIS query, which leaves the next query its own decision.
NOTE_EMPTY_QUERY = "empty query"
NOTE_FAILED = "search failed (provider error); the records already gathered are all you have for this query"
# Never "answer from the records already gathered": with a closed
# door that sentence is an instruction to guess. The runtime judges
# the run from its evidence either way (cell.py); this note saves the
# completions a well-behaved model would otherwise spend on rephrases,
# and keeps the OTHER tool open to it.
NOTE_DOOR_CLOSED = (
    "{tool} is unavailable for the rest of this task ({status}); do not call it again."
    " Use the records already gathered and any other tool you have, or leave outputs empty."
)

# The record label the model reads and the stored evidence line
# carries, per tool: "web" and "contacts" rather than the enum's
# snake_case, because these are prose the model and a human read.
RECORD_LABEL: dict[AgentTool, str] = {AgentTool.WEB_SEARCH: "web", AgentTool.FIND_CONTACTS: "contacts"}


# The door each tool runs through: web search takes the configured
# door, contacts pin the paid one (the LinkedIn x-rays need
# Google-grade SERPs).
def door_for(tool: AgentTool) -> str:
    return SearchProvider.DATAFORSEO if tool is AgentTool.FIND_CONTACTS else settings.SEARCH_PROVIDER


def door_status_for_tool(tool: AgentTool) -> SearchStatus:
    """A tool's door status BEFORE a run: what the catalog ships and
    what seeds the run's per-tool statuses."""
    return door_status(door_for(tool))


class EvidenceRecord(NamedTuple):
    """One pooled hit. `position` is 1-based and dense ACROSS THE WHOLE
    CELL RUN, not per search, so the numbered lines the model reads,
    the evidence stored on the outcome row, and the row drawer all
    speak one key. Display and ordering only: nothing cites it, since
    a citation the model types is a free-text integer that can point
    anywhere, and checking it deeper means matching against the
    answer, which is judgment.

    `tool` is which tool fetched the hit (RECORD_LABEL), so a reader
    can tell a general web result from a people result. NOT "door",
    which is this repo's word for a PROVIDER entry point; these are
    the runtime's own tools."""

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
    """One cell walk's TOOL state, the object the framework hands every
    tool call and validator (RunContext.deps) and run_cell reads after
    the run: the evidence pool (what grounding fences the answer to,
    beside the rendered prompt's own URLs, which the answerer holds),
    every call's outcome (what the bench
    renders as per-query diagnoses), each toggled tool's door status,
    and the time the doors took. The one thing on it that is not tool
    state is `judgement`: the answerer's slot, because a validator can
    write nowhere else; the answerer hands it back and no tool reads
    it. The model never sees any of this; it sees the tool's docstring
    and what the tool returns."""

    # The evidence POOL: every hit the tools returned, deduped by
    # canonical URL, numbered once across the whole run. What every
    # completion re-reads, what grounding fences to, what the run
    # stores. `evidence` and `urls` are views of it.
    records: list[EvidenceRecord] = field(default_factory=list)
    outcomes: list[SearchOutcome] = field(default_factory=list)
    # Parallel tool calls can interleave seen's check-then-add; the
    # cost is a duplicated evidence line, never a wrong answer, so no
    # lock guards it.
    seen: set[str] = field(default_factory=set)
    # Each TOGGLED tool's door status for this run: seeded before the
    # run from the doors' configuration (run_cell), then folded from
    # each answer as it arrives (record_tool_status), so it is final the
    # moment the run ends. Per tool on purpose: one door refusing says nothing
    # about the other. Sibling tool calls of one model turn run on
    # parallel threads, so two calls of one tool can each burn one
    # backoff before either records the closure; like `seen`, the cost
    # is a little extra waiting, never a wrong answer, so no lock.
    tool_status: dict[AgentTool, SearchStatus] = field(default_factory=dict)
    # Seconds this run spent waiting on its search doors, summed over
    # every call of every tool (the seam's own backoff included): the
    # fill worker's pace figure, so the sheet can say WHAT was slow.
    # Added under a lock: tool calls of one model turn run on parallel
    # threads, and a lost update here would quietly undercount.
    search_seconds: float = 0.0
    _timing: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # The answerer's slot (see the class docstring).
    judgement: AnswerJudgement = field(default_factory=AnswerJudgement)

    @property
    def evidence(self) -> list[str]:
        """The pool as the STORED lines (never sent to the model)."""
        return [record.line for record in self.records]

    @property
    def urls(self) -> list[str]:
        """The pool's hit URLs, structured, for grounding (never
        re-parsed from the display-formatted lines)."""
        return [record.url for record in self.records]

    # The tools whose door has answered at least once this run: a
    # later failure of a door that served does not change its status
    # (the row has its evidence), while a door that only ever failed
    # ends the run wearing its last failure.
    served: set[AgentTool] = field(default_factory=set)

    def tool_open(self, tool: AgentTool) -> bool:
        """Whether the tool may still be CALLED: only a closer shuts a
        door mid-run. A door wearing a provisional failure (unreachable,
        error, never served yet) is still asked, since the next query
        may get through."""
        return self.tool_status.get(tool, SearchStatus.OPEN) not in SEARCH_DOOR_CLOSERS

    def record_tool_status(self, tool: AgentTool, status: SearchStatus) -> None:
        """One door's word, folded into the tool's status for the run:
        open marks the door served (and clears a provisional failure);
        a closer closes it; any other failure is provisional, kept
        only while the door has not served."""
        if status is SearchStatus.OPEN:
            self.served.add(tool)
            self.tool_status[tool] = SearchStatus.OPEN
        elif status in SEARCH_DOOR_CLOSERS or tool not in self.served:
            self.tool_status[tool] = status

    def add_search_seconds(self, seconds: float) -> None:
        with self._timing:
            self.search_seconds += seconds


def web_search(ctx: RunContext[CellDeps], query: str) -> str:
    """Search the web. Returns JSON: {"records": [{"record": N,
    "tool": "web", "title", "url", "snippet"}]}, exactly as the search
    provider returned them."""
    query = _clamp_query(query.strip(), tool=AgentTool.WEB_SEARCH)
    if not query:
        return _result([], NOTE_EMPTY_QUERY)
    return _search_through(ctx.deps, query, tool=AgentTool.WEB_SEARCH)


def find_contacts(ctx: RunContext[CellDeps], query: str) -> str:
    """Find people's public profile pages matching the query (role,
    company, location terms; no site: operator needed). Returns JSON:
    {"records": [{"record": N, "tool": "contacts", "title", "url",
    "snippet"}]}, exactly as the search provider returned them."""
    query = re.sub(r"(?i)\bsite\s*:\s*\S+\s*", "", query).strip()
    if not query:
        return _result([], NOTE_EMPTY_QUERY)
    query = _clamp_query(f"site:{DEFAULT_PEOPLE_SITE} {query}", tool=AgentTool.FIND_CONTACTS)
    return _search_through(ctx.deps, query, tool=AgentTool.FIND_CONTACTS)


_TOOL_FUNCTIONS = {AgentTool.WEB_SEARCH: web_search, AgentTool.FIND_CONTACTS: find_contacts}


def toggled_tools(config: AgentConfig) -> list[AgentTool]:
    """The tools this config asks for, in the order the config lists
    them (which is the order a user sees the toggles, and the order a
    blank cell's cause is named in)."""
    return [tool for tool in AgentTool if getattr(config.tools, tool.value)]


def build_tools(config: AgentConfig, deps: CellDeps) -> list[Tool]:
    """What THIS config on THIS deploy may call: a toggled tool whose
    door is not open (per `deps.tool_status`, seeded by run_cell) is simply
    not offered; its status already says why."""
    return [Tool(_TOOL_FUNCTIONS[tool]) for tool in toggled_tools(config) if deps.tool_open(tool)]


def _clamp_query(query: str, *, tool: AgentTool) -> str:
    """The authored-value clamp at the metered boundary, LOGGED when it
    bites: a cut query is a different question than the model asked,
    and a model that keeps writing past the bound is worth knowing
    about (the tool docstring tells it nothing about the length)."""
    if len(query) <= QUERY_MAX_LENGTH:
        return query
    logger.warning(
        "%s query truncated from %d to %d chars: %r", tool, len(query), QUERY_MAX_LENGTH, query[:QUERY_MAX_LENGTH]
    )
    return query[:QUERY_MAX_LENGTH]


def _result(records: list[dict], note: str = "") -> str:
    """ONE shape for every tool return, so the model never has to tell
    a sentence from a payload."""
    return json.dumps({"records": records, "note": note} if note else {"records": records}, ensure_ascii=False)


def _closed_note(tool: AgentTool, status: SearchStatus) -> str:
    return _result(
        [], NOTE_DOOR_CLOSED.format(tool=tool.value.replace("_", " "), status=status.value.replace("_", " "))
    )


def _search_through(deps: CellDeps, query: str, *, tool: AgentTool) -> str:
    """ONE search through THIS tool's door, end to end. Three steps,
    and the model sees only the last: refuse a closed door before any
    spend; ask the door and record what it said on deps (the outcome,
    the door's status, the time it took); add the hits to the run's
    EVIDENCE POOL (deps.records, the numbered records every completion
    re-reads and grounding fences the answer to) and hand the model
    the ones this call added, as JSON. A closed door is refused without an
    outcome, so the stored searches record only what hit the wire (a
    door that just said slow down must not get five more queries in
    the next second). Only this tool's door: the other tool keeps its
    own status."""
    if not deps.tool_open(tool):
        return _closed_note(tool, deps.tool_status[tool])
    outcome = _ask_door(deps, query, tool=tool)
    if outcome.status in SEARCH_DOOR_CLOSERS:
        return _closed_note(tool, outcome.status)
    if outcome.failed:
        return _result([], NOTE_FAILED)
    return _result(_pool_hits(deps, outcome.hits, tool=tool))


def _ask_door(deps: CellDeps, query: str, *, tool: AgentTool) -> SearchOutcome:
    """The spend: the seam call through the tool's door, timed, and
    everything it said recorded on deps (the outcome for the audit,
    the door's status for the run: see CellDeps.record_tool_status)."""
    started = time.monotonic()
    try:
        answer = search(query, provider=door_for(tool))
    finally:
        deps.add_search_seconds(time.monotonic() - started)
    outcome = SearchOutcome(
        tool=tool,
        status=answer.status,
        provider=answer.provider,
        attempts=answer.attempts,
        query=query,
        hits=answer.hits,
    )
    deps.outcomes.append(outcome)
    deps.record_tool_status(tool, outcome.status)
    return outcome


def _pool_hits(deps: CellDeps, hits: list[SearchHit], *, tool: AgentTool) -> list[dict]:
    """The evidence pool: each new hit becomes a numbered record the
    model, the validator, and the stored evidence all share."""
    pooled = []
    for hit in hits:
        # Canonical dedupe: www./regional variants of one page pool
        # once (grounding compares canonically too).
        key = canonical_url(hit.url)
        if key in deps.seen:
            continue
        if len(deps.records) >= EVIDENCE_MAX_LINES:
            # A NAMED ceiling instead of an implicit one: the pool is
            # what every completion re-reads, so its size is a cost.
            break
        deps.seen.add(key)
        # Numbered ACROSS calls: the number is the citation key the
        # model, the validator, and the stored evidence all share.
        record = EvidenceRecord(
            position=len(deps.records) + 1,
            tool=RECORD_LABEL[tool],
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
        )
        deps.records.append(record)
        pooled.append(record.as_json())
    return pooled

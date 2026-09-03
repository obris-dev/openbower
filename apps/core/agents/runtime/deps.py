"""The per-run state every tool call and validator shares: the object
the framework hands them (RunContext.deps) and run_cell reads after the
run. The evidence pool (what grounding fences the answer to), every
call's outcome (the per-query diagnoses), each toggled tool's
status, and the time its calls took, all keyed by TOOL NAME, so the
state carries any registered tool without knowing one from another.
What a status MEANS (which codes close a tool) is the tool's own
declaration, so the folding verbs take the spec's closers rather than
reading a global: this module knows no tool and no registry.

The one thing here that is not tool state is `judgement`: the
answerer's slot, because a validator can write nowhere else; the
answerer hands it back and no tool reads it. The model never sees any
of this; it sees a tool's docstring and what the tool returns."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NamedTuple

from ..constants import ToolStatus
from .judgement import AnswerJudgement
from .outcomes import SearchOutcome


class EvidenceRecord(NamedTuple):
    """One pooled hit. `position` is 1-based and dense ACROSS THE WHOLE
    CELL RUN, not per search, so the numbered lines the model reads,
    the evidence stored on the outcome row, and the row drawer all
    speak one key. Display and ordering only: nothing cites it, since
    a citation the model types is a free-text integer that can point
    anywhere, and checking it deeper means matching against the
    answer, which is judgment.

    `tool` is which tool fetched the hit (the registry's record
    label), so a reader can tell a general web result from a people
    result: the runtime's own tools, never the provider behind
    them."""

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
    """One cell walk's TOOL state. See the module docstring; per-tool
    entries are keyed by the tool's registered NAME."""

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
    # Each TOGGLED tool's status for this run: seeded before the
    # run from each tool's availability (run_cell), then folded from
    # each answer as it arrives (record_tool_status), so it is final the
    # moment the run ends. Per tool on purpose: one tool refusing says
    # nothing about another. Sibling tool calls of one model turn run on
    # parallel threads, so two calls of one tool can each burn one
    # backoff before either records the closure; like `seen`, the cost
    # is a little extra waiting, never a wrong answer, so no lock.
    tool_status: dict[str, StrEnum] = field(default_factory=dict)
    # Seconds this run spent waiting on each tool's provider, keyed by
    # tool name and summed across that tool's calls (the seam's own
    # backoff included). A MAP, not a scalar: callers aggregate as
    # their surface needs (the fill worker sums it into its pace
    # figure; a per-tool view stays derivable). Added under a lock:
    # tool calls of one model turn run on parallel threads, and a lost
    # update here would quietly undercount.
    call_seconds: dict[str, float] = field(default_factory=dict)
    _timing: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # The answerer's slot (see the module docstring).
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

    # The tools that have SERVED at least once this run: a later
    # NON-CLOSING failure of a served tool does not change its status
    # (the row has its evidence); a CLOSER overwrites even a served
    # tool, and then sticks. A tool that only ever failed ends the run
    # wearing its last non-closing failure, or the first closer if one
    # landed.
    served: set[str] = field(default_factory=set)

    def tool_open(self, name: str, closers: frozenset[str]) -> bool:
        """Whether the tool may still be CALLED: only one of its own
        closers shuts it mid-run. A tool wearing a provisional failure
        (unreachable, error, never served yet) is still asked, since
        the next query may get through."""
        return self.tool_status.get(name, ToolStatus.OPEN) not in closers

    def record_tool_status(self, name: str, status: StrEnum, closers: frozenset[str]) -> None:
        """One call's word, folded into the tool's status for the run:
        open marks the tool served (and clears a provisional failure);
        a closer closes it AND STICKS; any other failure is
        provisional, kept only while the tool has not served.

        Closers are sticky because sibling tool calls run on parallel
        threads and land in any order: a slow success arriving after
        the closer must not reopen the tool (the model would re-buy a
        full backoff) or ship "open" on the task and cell for a tool
        that refused. The success still marks the tool SERVED, which
        is a historical fact the blank-cause skip reads."""
        if status == ToolStatus.OPEN:
            self.served.add(name)
            if self.tool_status.get(name) in closers:
                return
            self.tool_status[name] = status
        elif self.tool_status.get(name) in closers:
            return
        elif status in closers or name not in self.served:
            self.tool_status[name] = status

    def add_call_seconds(self, name: str, seconds: float) -> None:
        with self._timing:
            self.call_seconds[name] = self.call_seconds.get(name, 0.0) + seconds

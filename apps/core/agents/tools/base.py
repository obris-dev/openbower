"""The tool CONTRACT: what any tool declares to register, and the
helpers every tool shares whatever it talks to. Nothing
provider-shaped lives here (that is the `search` family package);
this module imports no runtime and no seam, so the contract a
contributor reads is self-contained.

A tool is a module declaring a SPEC (`ToolSpec`) and registering it in
`tools.registry`: the model-facing function, the labels its records
and failures wear, its availability, the failure MODE each of its own
codes carries, and the tier-1 copy a fill failure quotes. Everything
the runtime and its surfaces need to know about one tool lives on the
spec, so adding a tool is adding a module and one register() call,
never finding a second hand-written map."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, NamedTuple

from ..constants import QUERY_MAX_LENGTH

logger = logging.getLogger(__name__)

# A registered tool name is a code token (the config toggle key, the
# stored tools map's key, the wire's vocabulary): bounded like every
# authored value (binary, far above any real name).
TOOL_NAME_MAX_LENGTH = 64

# One of a tool's OWN failure codes ("rate_limited", "not_configured"):
# tool-owned strings, deliberately an open vocabulary (a new tool's
# codes are its own to invent), so a named alias rather than an enum.
# The keys of failure_modes are the declared set.
type FailureCode = str

# The tool notes are ONE small vocabulary, so the model never reads
# prose that varies. Each one says what the records list cannot: why
# it is empty, and what to do about it. Only a closed tool tells the
# model to stop calling it; the others say the pool is what it
# has for THIS query, which leaves the next query its own decision.
NOTE_EMPTY_QUERY = "empty query"
# Never "answer from the records already gathered": with a closed
# tool that sentence is an instruction to guess. The runtime judges
# the run from its evidence either way (runtime/cell.py); this note
# saves the completions a well-behaved model would otherwise spend on
# rephrases, and keeps the OTHER tool open to it.
NOTE_TOOL_CLOSED = (
    "{tool} is unavailable for the rest of this task ({status}); do not call it again."
    " Use the records already gathered and any other tool you have, or leave outputs empty."
)


class FailureMode(StrEnum):
    """How one of a tool's OWN FAILURE codes behaves, the one judgment
    a tool author can actually make about a failure of theirs, and the
    whole of what the runtime reads off it. The mode carries BOTH time
    horizons: whether the tool may still be CALLED within this run
    (closer-ness derives from it, never declared separately), and how
    the failure reads ACROSS runs (which sheet state a mode means is
    the cell runner's verdict, never the tool's). FAILURES only: the
    open/served state is not a failure mode and never appears in a
    failure_modes map ("open" stays a STATUS, because a stored "open"
    means toggled-and-healthy, which absence cannot say). The DETAIL
    code stays the tool's own and rides the record beside the verdict
    as the why."""

    # ONE query failed; the tool stays callable (a flaky failure must
    # not kill the tool for the run's remaining budget, since the next
    # query may get through). Across runs it reads like TRANSIENT.
    HAZARD = "hazard"
    # The PROVIDER declared a state (a throttle): believe it, stop asking
    # for the rest of this run; across runs, asking again later could
    # change it, so the row is worth re-buying.
    TRANSIENT = "transient"
    # This deploy or config cannot serve; closed for this run AND a
    # retry re-buys the same refusal until something outside the run
    # changes.
    FATAL = "fatal"


class ToolError(Exception):
    """The BASE of every tool's failure vocabulary: a tool DECLARES a
    failure by subclassing (`code` and `mode` as ClassVars, the single
    source the spec's failure_modes view derives from) and REPORTS one
    by raising the subclass, optionally with a model-facing NOTE (the
    sentence returned in the payload's `note` slot; the harness
    supplies a generic one otherwise). The harness catches THIS base
    at the per-call boundary, so it handles every tool's subclasses
    without knowing any of them: nothing a tool raises ever reaches
    the framework, because one tool's failure must not abort the run.
    Chain the underlying exception (`raise ... from e`): the harness
    logs the cause, and the note stays AUTHORED copy, never a library
    message that could leak a URL or a key."""

    code: ClassVar[FailureCode] = ""
    mode: ClassVar[FailureMode]

    def __init__(self, note: str = "") -> None:
        super().__init__(type(self).code)
        self.note = note


class FailureCopy(NamedTuple):
    """The tier-1 pieces of a fill failure a tool's provider caused:
    `problem` names the tool, what its provider said, and which
    provider; `remedy` is the next step where one exists ("" otherwise). The
    breaker composes the sentence; the tool authors the facts."""

    problem: str
    remedy: str


@dataclass(frozen=True)
class ToolSpec:
    """Everything the runtime and its surfaces need to know about ONE
    tool, in one place. Registration (`tools.registry.register`)
    validates the whole spec before it enters the registry, so an
    incomplete tool is a loud import-time error, never a quiet gap a
    row discovers later.

    The names CASCADE: `name` IS the function's own name (a property,
    never declared, because pydantic-ai exposes function.__name__ as
    the callable the model sees, so a separate declaration could only
    drift from what the model actually calls); `record_label` defaults
    to the name; `display_name` defaults to the name prettified.
    Override the two labels for taste, never the name."""

    # The model-facing function: (RunContext[CellDeps], query) -> the
    # JSON payload string. Its docstring is what the model reads, and
    # its NAME is the tool's registered name (the config's toggle key,
    # the key every stored tools map carries).
    function: Callable[..., str]
    # The tool's availability on THIS deploy, asked before a run: a
    # STATUS, not a bool, because the why (not_configured vs
    # rate_limited) drives the cell state, Continue, and the failure
    # copy. Seeds the run's per-tool status and gates whether the tool
    # is offered at all. The ONE convention: a tool that can serve
    # answers "open" (ToolStatus.OPEN's value); any other answer must
    # be a key of failure_modes, whose keys ARE the tool's declared
    # failure vocabulary (there is no separate statuses declaration to
    # drift from it).
    availability: Callable[[], StrEnum]
    # The tool's DECLARED failure vocabulary: its ToolError subclasses,
    # each carrying code and mode (registration validates the classes).
    # failure_modes below is the derived view every consumer reads; a
    # failure exists exactly once, as the exception the tool raises.
    errors: tuple[type[ToolError], ...]
    # failure code -> the tier-1 copy for a fill this tool failed.
    failure_copy: Callable[[FailureCode], FailureCopy]
    # WHERE this tool stands in the blame walk: a blank cell's cause
    # is named by the LOWEST blame_order among the toggled tools that
    # closed unserved. A DECLARED fact, never the import or walk
    # order, and UNIQUE across the roster (the register guard refuses
    # a taken number, naming the current ranking, so placing a new
    # tool means reading it, never guessing).
    blame_order: int
    # The label the model reads on evidence records; defaults to the
    # name, overridden with shorter prose ("web") when the name reads
    # long in a record line.
    record_label: str = ""
    # The name a user reads (fill failure copy); defaults to the name
    # prettified ("web_search" -> "Web search").
    display_name: str = ""

    def __post_init__(self) -> None:
        # frozen=True blocks plain assignment; construction-time
        # defaulting is the documented escape hatch.
        if not self.record_label:
            object.__setattr__(self, "record_label", self.name)
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name.replace("_", " ").capitalize())

    @property
    def name(self) -> str:
        return self.function.__name__

    @property
    def failure_modes(self) -> Mapping[FailureCode, FailureMode]:
        """DERIVED from the declared error classes, never stated
        twice: code -> mode for every failure the tool can raise."""
        return {error.code: error.mode for error in self.errors}

    @property
    def closers(self) -> frozenset[FailureCode]:
        """The codes that CLOSE this tool for the rest of the run,
        DERIVED from the modes, never declared: every failure closes
        except a per-query HAZARD, whose next query may get
        through."""
        return frozenset(code for code, mode in self.failure_modes.items() if mode is not FailureMode.HAZARD)


def family_errors(family: type[ToolError]) -> tuple[type[ToolError], ...]:
    """A family's declared errors, DERIVED from inheritance: every
    subclass of the family base, TRANSITIVELY, in definition order.
    Deriving is what makes a new error class impossible to forget in a
    hand-kept tuple: subclassing IS the membership, at any depth, so a
    member specialized from another member (raisable wherever its
    parent is) can never be raisable-but-undeclared. Call it at the
    family module's bottom (after every definition) and snapshot the
    result. The family base itself is never a member; every subclass
    IS one, so a codeless intermediate grouping class refuses at
    registration on purpose (an intermediate is raisable too, and a
    raisable class without a code is the exact gap this closes)."""
    found: list[type[ToolError]] = []
    frontier = list(family.__subclasses__())
    while frontier:
        member = frontier.pop(0)
        found.append(member)
        frontier.extend(member.__subclasses__())
    return tuple(found)


def clamp_query(query: str, *, name: str) -> str:
    """The authored-value clamp at the metered boundary, LOGGED when it
    bites: a cut query is a different question than the model asked,
    and a model that keeps writing past the bound is worth knowing
    about (the tool docstring tells it nothing about the length)."""
    if len(query) <= QUERY_MAX_LENGTH:
        return query
    logger.warning(
        "%s query truncated from %d to %d chars: %r", name, len(query), QUERY_MAX_LENGTH, query[:QUERY_MAX_LENGTH]
    )
    return query[:QUERY_MAX_LENGTH]


def result_json(records: list[dict], note: str = "") -> str:
    """ONE shape for every tool return, so the model never has to tell
    a sentence from a payload."""
    return json.dumps({"records": records, "note": note} if note else {"records": records}, ensure_ascii=False)


def closed_note(name: str, code: FailureCode) -> str:
    # The tool's CALLABLE name, verbatim: the note says "do not call
    # it again", so the useful token is the name in the model's own
    # tool list, not a prose rendering of it. `code` is a FailureCode
    # (a StrEnum member IS one), so the note works for any tool's
    # vocabulary.
    return result_json([], NOTE_TOOL_CLOSED.format(tool=name, status=str(code).replace("_", " ")))


__all__ = [
    "NOTE_EMPTY_QUERY",
    "NOTE_TOOL_CLOSED",
    "TOOL_NAME_MAX_LENGTH",
    "FailureCode",
    "FailureCopy",
    "FailureMode",
    "ToolError",
    "ToolSpec",
    "clamp_query",
    "closed_note",
    "family_errors",
    "result_json",
]

"""The call's FACTS: what one answer call gathered and observed,
projected off the internal machinery (deps) at the boundary. This is
the shape that rides BOTH exits: the happy Answer carries it, and every
AgentError carries it, because the diagnostics must survive precisely
the failures (a parked row's stored tool calls are the audit, the tools
map is what the breaker attributes from). Facts only, never verdicts:
no cells' causes and no sheet states live here; assigning meaning to
the record is the caller's (run_cell's) job."""

from __future__ import annotations

from typing import NamedTuple

from ...tools.base import FailureMode
from ..deps import CellDeps
from ..outcomes import SearchOutcome


class CallRecord(NamedTuple):
    """One call's diagnostics. `tools` is each toggled tool's final
    status code; `served` the tools that answered at least once (the
    blank-naming walk's exemption); `assessments` the
    model's confidence and reason per answered output, dropped values
    included; `verification_dropped` whether the floor discarded any
    answered field (an all-blank row with drops reads UNVERIFIED,
    never a bare no-evidence)."""

    evidence: list[str]
    tool_calls: list[SearchOutcome]
    tools: dict[str, str]
    served: frozenset[str]
    assessments: dict
    tool_call_seconds: dict[str, float]
    verification_dropped: bool
    # The FAILURE MODE of the blaming tool, when one exists: the first
    # toggled tool (in blame order) that failed without ever serving.
    # A FACT the answerer derives (it owns the registry, the toggles,
    # and the served-exemption doctrine); which sheet state a mode
    # means stays the caller's verdict. None = no tool takes the
    # blame. The tool's detail code stays in `tools` beside it.
    tool_failure: FailureMode | None = None
    # WHICH tool takes the blame ("" when none does): derived by the
    # same walk in the same pass, so a consumer can never re-derive it
    # from `tools` alone and disagree (the served exemption is not
    # reconstructible from the status map).
    blamed_tool: str = ""

    @classmethod
    def from_deps(cls, deps: CellDeps, *, blamed_tool: str = "", tool_failure: FailureMode | None = None) -> CallRecord:
        """The ONE projector: deps dies at this line, and nothing
        outside the answerer ever holds the machinery."""
        return cls(
            evidence=deps.evidence,
            tool_calls=list(deps.outcomes),
            tools={name: str(status) for name, status in deps.tool_status.items()},
            served=frozenset(deps.served),
            assessments=deps.judgement.assessments,
            tool_call_seconds=dict(deps.call_seconds),
            verification_dropped=deps.judgement.verification_dropped,
            tool_failure=tool_failure,
            blamed_tool=blamed_tool,
        )


# The record of a call that never got far enough to gather anything
# (an empty render): every field honestly empty.
EMPTY_RECORD = CallRecord([], [], {}, frozenset(), {}, {}, False)


class Answer(NamedTuple):
    """The happy return: the call COMPLETED, whatever its verdict.
    `cells` may be {} (an honest "found nothing" is a verdict, not an
    error) or partial (a run answers outputs independently); the
    record carries the diagnostics either way. Which cause an
    unanswered column wears is NOT decided here: that is sheet
    vocabulary, assigned by the caller from the record."""

    cells: dict[str, str]
    record: CallRecord

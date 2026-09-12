"""The fill lane's harness over the agents runtime: run_cell(config,
row_data) -> CellRun, assigning SHEET MEANING to what the answerer
reports. It lives in LISTS because the verdicts are sheet vocabulary
(StoredCellState), completing the set this app already owns (the park
policy, the landing, the cell truth); agents exports only the
answerer's FACTS (Answer, the AgentError family, CallRecord). The
answerer speaks its own vocabulary (an Answer for every completed
call, typed AgentErrors with the facts aboard for every failed one,
refusals for a call that cannot happen); this module maps each to the
stored cell vocabulary and packs the one record the runtime hands out.
Facts come from the answerer; verdicts are assigned here.

There is NO forced-search fallback: a model that never engages its
tools writes blank cells with the diagnosis saying so, and the fix is
picking a more capable model, not a second code path re-searching on a
guess. A typed AgentConfig (the contract model) drives it, never an
Agent row: the row is one custody for a config, the column's
quick-prompt path is the other, and the test bench runs drafts that
are neither. A tool's trouble is PER TOOL and rides out as data: a
grounded answer lands whatever another tool reported, and the
statuses go on the task and the cell for the user to act on."""

from __future__ import annotations

import logging
from typing import NamedTuple

from agents.runtime.answer import (
    EMPTY_RECORD,
    AgentError,
    AgentResponseInvalid,
    AgentUnableToRespond,
    CallRecord,
    CellAnswerer,
    EmptyRender,
    NoAvailableTools,
)
from agents.runtime.outcomes import SearchOutcome
from agents.tools.base import FailureMode
from openbower_schema.agents import AgentConfig

from ...constants import StoredCellState

logger = logging.getLogger(__name__)

__all__ = ["CellRun", "run_cell"]


class CellRun(NamedTuple):
    """One row's walk AND its diagnostics, the ONE record the runtime
    hands out: `cells` is what a fill would write ({} = nothing),
    `evidence` what the model saw (grounding's fence, the bench's
    disclosure), `tool_calls` each call's outcome (a throttled provider
    must not read as a bad agent), `tools` each toggled tool's final
    status for this run (the task's record, the cell's mark).
    Every return path carries its diagnostics by construction: the
    blank-cell case is exactly the one that needs its why."""

    cells: dict[str, str]
    evidence: list[str]
    tool_calls: list[SearchOutcome]
    # The cause an UNANSWERED output carries (a StoredCellState
    # value): the run's ONE verdict field. Row-level readings derive
    # (no cells + a retry cause parks; a partial answer's silent
    # columns wear it), so there is no second field to drift from it.
    declined_cause: str = ""
    # key -> the model's confidence and the reason it gave, for every
    # answered output INCLUDING the ones the floor discarded (those
    # also carry the value under `dropped`). The per-answer audit
    # trail: a blank whose why is recoverable, and the only place the
    # rejected distribution exists.
    assessments: dict = {}
    # tool -> its status code at the end of the run, for every
    # toggled tool ("open" when it served). The fill operation records
    # it on the task and the cell, filled or blank alike.
    tools: dict[str, str] = {}
    # Seconds the run spent waiting on each tool's provider, keyed by tool
    # name and summed across that tool's calls. A MAP so callers
    # aggregate as their surface needs (the worker sums it into its
    # pace figure; a per-tool view stays derivable).
    tool_call_seconds: dict[str, float] = {}
    # WHICH tool the run blames for its blanks ("" when none): the
    # answerer's own walk, carried so downstream copy (the breaker's
    # tier-1 sentence) quotes the culprit instead of re-deriving it
    # from `tools`, which cannot see the served exemption.
    blamed_tool: str = ""


# The FATAL failure modes' stored states, most specific first with the
# base LAST (the unknown failure). Retriable modes are not here: every
# one of them reads TRANSIENT by the declared `retriable` fact itself,
# so a new retriable type can never be forgotten in a table.
_FATAL_STATES: tuple[tuple[type[AgentError], StoredCellState], ...] = (
    (AgentUnableToRespond, StoredCellState.NO_ANSWER),
    (AgentResponseInvalid, StoredCellState.UNPARSEABLE),
    (AgentError, StoredCellState.MODEL_ERROR),
)


def _state_for(failure: AgentError) -> StoredCellState:
    """The stored cell state a call failure maps to: the harness's
    verdict on the answerer's fact."""
    if failure.retriable:
        return StoredCellState.TRANSIENT
    return next(state for klass, state in _FATAL_STATES if isinstance(failure, klass))


# The stored cell state each tool FAILURE MODE maps to: the
# harness's verdict on the tool's declared fact (the tool's own detail
# code rides the tools map beside it as the why). FATAL reads
# TOOL_NOT_CONFIGURED because configuration is the only fatal tool
# detail that exists; the day a tool declares a fatal mode that is not
# configuration, the sheet vocabulary decision happens HERE, never on
# the tool.
_TOOL_FAILURE_MODE_STATES: dict[FailureMode, StoredCellState] = {
    # A hazard differs from a transient only WITHIN the run (the tool
    # stays callable); across runs both read as a tool that did not
    # serve, so both wear the same retriable state.
    FailureMode.HAZARD: StoredCellState.TOOL_UNAVAILABLE,
    FailureMode.TRANSIENT: StoredCellState.TOOL_UNAVAILABLE,
    FailureMode.FATAL: StoredCellState.TOOL_NOT_CONFIGURED,
}


def _name_declined(record: CallRecord) -> str:
    """WHY an unanswered output of a COMPLETED call is blank, in rank
    order, all VERDICTS over the record's facts: a blaming tool's
    failure mode (the answerer derived which tool, if any, takes the
    blame; the tool's detail code rides the tools map beside the
    state); then verification drops read UNVERIFIED (an answer
    arrived; nothing confirmed it); otherwise the model honestly
    declined, which reads NO_EVIDENCE.

    The same pick reads as the whole row's blank when nothing landed
    and rides beside a partial answer's cells (no park, which would
    hold hostage the cells that answered); a later Continue re-targets
    the silent columns either way."""
    if record.tool_failure is not None:
        return _TOOL_FAILURE_MODE_STATES[record.tool_failure]
    return StoredCellState.UNVERIFIED if record.verification_dropped else StoredCellState.NO_EVIDENCE


def _finish(cells: dict[str, str], record: CallRecord, declined: str) -> CellRun:
    """The ONE packer every path exits through: the record's facts plus
    the verdicts this module assigned."""
    logger.info("cell: %d evidence hits -> outputs %s | tools %s", len(record.evidence), sorted(cells), record.tools)
    return CellRun(
        cells,
        record.evidence,
        record.tool_calls,
        declined,
        # NOT filtered to the landed cells: a dropped answer is the
        # case an audit trail exists for.
        record.assessments,
        record.tools,
        record.tool_call_seconds,
        record.blamed_tool,
    )


def run_cell(config: AgentConfig, row_data: dict) -> CellRun:
    """One row's walk, from exactly TWO inputs: the config (the model,
    the schema, the toggles all derive from it) and the row. Cells are
    keyed by the config's OWN output keys: the runtime speaks
    config-local names, and mapping them onto a sheet's row-data keys
    is the fill's concern, not the runtime's. Construction resolves
    the model from the config, its one source, raising ModelUnavailable
    config-tier before anything else can decide a blank."""
    try:
        answered = CellAnswerer(config).answer(row_data)
    except EmptyRender:
        # The one all-blank row a config can legitimately produce:
        # every {{token}} rendered empty. Diagnosed by emptiness
        # itself; nothing was asked, so nothing is spent and no tool
        # is blamed.
        logger.info("cell: prompt rendered empty; writing nothing")
        return _finish({}, EMPTY_RECORD, StoredCellState.NO_EVIDENCE)
    except NoAvailableTools as refusal:
        # Refused before spend; the record's seeded statuses are the
        # why, and the naming walk reads the blame off them.
        logger.info("cell: tools toggled but none available; writing nothing without spending")
        return _finish({}, refusal.record, _name_declined(refusal.record))
    except AgentError as failure:
        # The call itself failed: the failure mode is the cause, for
        # the blank and the declined leg alike (nothing was answered).
        return _finish({}, failure.record, _state_for(failure))
    return _finish(answered.cells, answered.record, _name_declined(answered.record))

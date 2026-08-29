"""The per-row WALK, agentic on pydantic-ai: render the prompt, seed
each toggled tool's door status, build the tools the toggles and doors
allow, let the model drive inside its budget, and hold the doctrine on
the way out. There is NO forced-search fallback: a model that never
engages its tools writes blank cells with the diagnosis saying so, and
the fix is picking a more capable model, not a second code path
re-searching on a guess. A typed AgentConfig (the contract model)
drives it, never an Agent row: the row is one custody for a config,
the column's quick-prompt path is the other, and the test bench runs
drafts that are neither.

The doctrine holds on every path: tools toggled + zero evidence
surfaced = blank cells (the model chooses when and how to search,
never whether evidence is required), and URLs ground to the evidence
pool. A door's trouble is PER TOOL and rides out as data: a grounded
answer lands whatever another tool's door said, and the statuses go
on the task and the cell for the user to act on."""

from __future__ import annotations

import logging
from typing import NamedTuple

from pydantic_ai.models import Model

from lists.constants import CELL_STATE_BY_STATUS, StoredCellState
from openbower_schema.agents import AgentConfig

from ..constants import AgentTool, SearchStatus
from ..providers import model_for
from .answer import CellAnswerer
from .outcomes import SearchOutcome
from .prompts import render_prompt
from .tools import CellDeps, build_tools, door_status_for_tool, toggled_tools

logger = logging.getLogger(__name__)


class CellRun(NamedTuple):
    """One row's walk AND its diagnostics: `cells` is what a fill would
    write ({} = nothing), `evidence` what the model saw (grounding's
    fence, the bench's disclosure), `searches` each query's outcome (a
    throttled provider must not read as a bad agent), `tools` each
    toggled tool's final door status for this run (the task's record,
    the cell's mark). Every return path carries its diagnostics by
    construction: the blank-cell case is exactly the one that needs its
    why."""

    cells: dict[str, str]
    evidence: list[str]
    searches: list[SearchOutcome]
    # WHY cells is empty (a StoredCellState value; "" when cells landed):
    # the fill worker's outcome, straight off the run. The retry causes
    # park; everything else is a terminal diagnosed blank.
    blank_cause: str = ""
    # The cause an UNANSWERED output carries when the run answered
    # others: a partially answered row settles only what it answered,
    # so its silent outputs need their own why.
    declined_cause: str = ""
    # key -> the model's confidence and the reason it gave, for every
    # answered output INCLUDING the ones the floor discarded (those
    # also carry the value under `dropped`). The per-answer audit
    # trail: a blank whose why is recoverable, and the only place the
    # rejected distribution exists.
    assessments: dict = {}
    # tool -> its door's status code at the end of the run, for every
    # toggled tool ("open" when it served). The fill operation records
    # it on the task and the cell, filled or blank alike.
    tools: dict[str, str] = {}


def _answer(config: AgentConfig, prompt: str, answerer: CellAnswerer, deps: CellDeps):
    """The validated answer, or None for blank cells: ONE call (tools
    are a parameter; the run fills deps through RunContext as the model
    calls them), then the doctrine guards. No salvage anywhere: no
    validated answer is SIGNAL. And no SPEND on a decidable blank:
    tools toggled with every door closed can never produce evidence,
    so that guard fires BEFORE a completion is bought."""
    tools = build_tools(config, deps)
    if config.uses_tools and not tools:
        logger.info("cell: tools toggled but no door open; writing nothing without spending")
        return None
    answer = answerer.answer(prompt, tools, deps)
    if config.uses_tools and not deps.evidence:
        # Tools meant to ground the answer produced no evidence:
        # writing from model memory is exactly the fabrication path,
        # so even a validated answer is discarded. The why is settled
        # by _settle_doors: a door that never answered is a retry, a
        # door that answered nothing is a diagnosis.
        logger.info("cell: tools enabled but no evidence; writing nothing")
        return None
    return answer


def _settle_doors(config: AgentConfig, deps: CellDeps) -> None:
    """Each toggled tool's FINAL status for the run. A door the pool
    closed keeps its status. A door that was asked, never answered
    (every outcome non-open), and contributed no evidence takes its
    last outcome's status: a single timeout does not close a door
    mid-run (the next query may get through), but a door that only
    ever failed is a door that did not serve this row. A door that
    served, or was never asked, stays open."""
    for tool in toggled_tools(config):
        if not deps.tool_open(tool):
            continue
        asked = [outcome for outcome in deps.outcomes if outcome.tool is tool]
        if asked and all(outcome.failed for outcome in asked):
            deps.doors[tool] = asked[-1].status


def _blank_cause(config: AgentConfig, deps: CellDeps) -> str:
    """WHY a run with no cells is blank, in rank order: the answerer's
    own cause (a model transient, a validation miss) outranks the
    doctrine's; then the first toggled tool whose door is not open
    names the cell (the sheet keys on the BASE code, the tool and its
    own code ride the record beside it); then verification drops read
    UNVERIFIED (an answer arrived; nothing confirmed it); otherwise the
    model honestly declined, which reads NO_EVIDENCE."""
    if deps.blank_cause:
        return deps.blank_cause
    for tool in toggled_tools(config):
        if not deps.tool_open(tool):
            return CELL_STATE_BY_STATUS[deps.doors[tool]]
    return StoredCellState.UNVERIFIED if deps.verification_dropped else StoredCellState.NO_EVIDENCE


def run_cell(
    config: AgentConfig, row_data: dict, *, model: Model | None = None, deps: CellDeps | None = None
) -> CellRun:
    """One row's walk. Cells are keyed by the config's OWN output keys:
    the runtime speaks config-local names, and mapping them onto a
    sheet's row-data keys is the fill's concern (phase 5), not the
    runtime's."""
    prompt = render_prompt(config.prompt, row_data).strip()
    if not prompt:
        # The one all-blank row a config can legitimately produce: every
        # {{token}} rendered empty. Diagnosed by emptiness itself.
        logger.info("cell: prompt rendered empty; writing nothing")
        return CellRun({}, [], [], StoredCellState.NO_EVIDENCE, StoredCellState.NO_EVIDENCE, {}, {})
    # Raises ModelUnavailable on an unrunnable address: a config error
    # fails the run loudly; only per-row hazards degrade to blanks. A
    # caller that already resolved the address (the test POST's 400
    # gate) passes its model instead of resolving twice.
    if model is None:
        model = model_for(config.provider, config.source, config.model)
    # A caller-built deps carries the caller's doors and callbacks (the
    # fill worker's Standard search client and lease renewal); the
    # prompt and the door statuses are stamped here either way, one
    # construction path.
    deps = deps if deps is not None else CellDeps()
    deps.prompt = prompt
    for tool in toggled_tools(config):
        deps.doors[tool] = door_status_for_tool(tool)
    answer = _answer(config, prompt, CellAnswerer(model, config.outputs), deps)
    _settle_doors(config, deps)
    # Validation, stripping, clamping, grounding, and provenance
    # verification all happened inside the framework run; what's left
    # is keeping non-blank OUTPUT values (the companion confidence
    # fields travel via deps.assessments, never as cells).
    dump = answer.model_dump() if answer is not None else {}
    cells = {output.key: dump[output.key] for output in config.outputs if dump.get(output.key)}
    declined = _blank_cause(config, deps)
    cause = "" if cells else declined
    tools = {tool.value: status.value for tool, status in deps.doors.items()}
    logger.info("cell: %d evidence hits -> outputs %s | doors %s", len(deps.evidence), sorted(cells), tools)
    return CellRun(
        cells,
        deps.evidence,
        deps.outcomes,
        cause,
        declined,
        # NOT filtered to the landed cells: a dropped answer is the
        # case an audit trail exists for.
        deps.assessments,
        tools,
    )


__all__ = ["AgentTool", "CellRun", "SearchStatus", "run_cell"]

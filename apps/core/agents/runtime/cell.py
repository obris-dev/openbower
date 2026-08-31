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

from ..constants import AgentTool, SearchStatus, ToolStatus
from ..providers import model_for
from .answer import Answered, CellAnswerer
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


def _answer(config: AgentConfig, prompt: str, answerer: CellAnswerer, deps: CellDeps) -> Answered:
    """The answer call and the doctrine guards around it: ONE call
    (tools are a parameter; the run fills deps through RunContext as
    the model calls them). No salvage anywhere: no validated answer is
    SIGNAL. And no SPEND on a decidable blank: tools toggled with every
    door closed can never produce evidence, so that guard fires BEFORE
    a completion is bought."""
    tools = build_tools(config, deps)
    if config.uses_tools and not tools:
        logger.info("cell: tools toggled but no door open; writing nothing without spending")
        return Answered(None, "", deps.judgement)
    answered = answerer.answer(prompt, tools, deps)
    if config.uses_tools and not deps.records:
        # Tools meant to ground the answer produced no evidence:
        # writing from model memory is exactly the fabrication path,
        # so even a validated answer is discarded. The why is on the
        # doors: a door that never answered is a retry, a door that
        # answered nothing is a diagnosis.
        logger.info("cell: tools enabled but no evidence; writing nothing")
        return Answered(None, answered.cause, answered.judgement)
    return answered


def _cells(config: AgentConfig, answered: Answered) -> dict[str, str]:
    """The cells this row would write, off the validated answer.

    The answer type carries THREE fields per declared output: the
    value, the model's reason for its score, and the score, e.g.

        {"person": "Jane Doe",
         "person_bwr_confidence_reason": "Record 2 is her profile ...",
         "person_bwr_confidence": 0.95,
         "profile": "",
         "profile_bwr_confidence_reason": "No record showed one.",
         "profile_bwr_confidence": 0.0}

    Only the DECLARED outputs are cells, so the walk is over
    config.outputs, never over the dump: the companion fields ride
    the judgement into the stored run as the audit, never a column.
    And an empty value is the model declining that output (the
    instructions ask for "" when it found nothing), so it is not a
    cell either: the column stays unanswered and takes the run's
    declined cause instead. The example lands {"person": "Jane Doe"}.

    Validation, stripping, clamping, grounding, and the confidence
    floor all ran inside the framework already: a value that scored
    under the floor is "" here, kept under `dropped` on its
    assessment."""
    if answered.output is None:
        return {}
    dump = answered.output.model_dump()
    return {output.key: dump[output.key] for output in config.outputs if dump.get(output.key)}


def _blank_cause(config: AgentConfig, deps: CellDeps, answered: Answered) -> str:
    """WHY a run with no cells is blank, in rank order: the answerer's
    own cause (a model transient, a validation miss) outranks the
    doctrine's; then the first toggled tool whose door is not open AND
    never served names the cell (the sheet keys on the status code,
    the tool and its code ride the record beside it); then
    verification drops read UNVERIFIED (an answer arrived; nothing
    confirmed it); otherwise the model honestly declined, which reads
    NO_EVIDENCE.

    A door that SERVED cannot name the blank: it gave the model real
    evidence, so a decline over that evidence is the model's verdict,
    not the door's fault, and diagnosing the door would park the row
    to re-buy the same verdict. A toggled door that was never even
    offered (not configured) DOES name it, deliberately: the missing
    tool may be exactly why the output is empty, the state is written
    at once, and Continue re-runs it once the door is set up (door
    credentials live in deployment settings, outside the config
    fingerprint, so no settled state could re-open on setup)."""
    if answered.cause:
        return answered.cause
    for tool in toggled_tools(config):
        if tool in deps.served:
            continue
        # Value compare against the BASE, never identity against one
        # tool's enum: a second tool's own OPEN member must read as
        # open here, not fall into the table as a KeyError.
        status = deps.tool_status.get(tool, SearchStatus.OPEN)
        if status != ToolStatus.OPEN:
            return CELL_STATE_BY_STATUS[status]
    return StoredCellState.UNVERIFIED if answered.judgement.verification_dropped else StoredCellState.NO_EVIDENCE


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
    # A caller-built deps is the caller's to read after the run; the
    # door statuses are seeded here either way, one construction path.
    deps = deps if deps is not None else CellDeps()
    for tool in toggled_tools(config):
        deps.tool_status[tool] = door_status_for_tool(tool)
    answered = _answer(config, prompt, CellAnswerer(model, config.outputs), deps)
    cells = _cells(config, answered)
    declined = _blank_cause(config, deps, answered)
    cause = "" if cells else declined
    tools = {tool.value: status.value for tool, status in deps.tool_status.items()}
    logger.info("cell: %d evidence hits -> outputs %s | tools %s", len(deps.evidence), sorted(cells), tools)
    return CellRun(
        cells,
        deps.evidence,
        deps.outcomes,
        cause,
        declined,
        # NOT filtered to the landed cells: a dropped answer is the
        # case an audit trail exists for.
        answered.judgement.assessments,
        tools,
    )


__all__ = ["AgentTool", "CellRun", "SearchStatus", "run_cell"]

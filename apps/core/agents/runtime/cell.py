"""The per-row WALK, agentic on pydantic-ai: render the prompt, build
the tools the toggles and doors allow, let the model drive inside its
budget, and hold the doctrine on the way out. There is NO
forced-search fallback: a model that never engages its tools
writes blank cells with the diagnosis saying so, and the fix is
picking a more capable model, not a second code path re-searching on a
guess. A typed AgentConfig (the contract model) drives it, never an
Agent row: the row is one custody for a config, the column's
quick-prompt path is the other, and the test bench runs drafts that
are neither.

The doctrine holds on every path: tools toggled + zero evidence
surfaced = blank cells (the model chooses when and how to search,
never whether evidence is required), and URLs ground to the evidence
pool."""

from __future__ import annotations

import logging
from typing import NamedTuple

from pydantic_ai.models import Model

from lists.constants import THROTTLED_STATE_BY_TOOL, StoredCellState
from openbower_schema.agents import AgentConfig

from ..constants import AgentTool
from ..providers import model_for
from ..search import SearchOutcome
from .answer import CellAnswerer
from .prompts import render_prompt
from .tools import CellDeps, build_tools

logger = logging.getLogger(__name__)


class CellRun(NamedTuple):
    """One row's walk AND its diagnostics: `cells` is what a fill would
    write ({} = nothing), `evidence` what the model saw (grounding's
    fence, the bench's disclosure), `searches` each query's diagnosis
    (hits vs failed; a throttled provider must not read as a bad
    agent). Every return path carries its diagnostics by construction:
    the blank-cell case is exactly the one that needs its why."""

    cells: dict[str, str]
    evidence: list[str]
    searches: list[SearchOutcome]
    # WHY cells is empty (a StoredCellState value; "" when cells landed):
    # the fill worker's outcome, straight off the run. transient means
    # retry; everything else is a terminal diagnosed blank.
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


def _answer(config: AgentConfig, prompt: str, answerer: CellAnswerer, deps: CellDeps):
    """The validated answer, or None for blank cells: ONE call (tools
    are a parameter; the run fills deps through RunContext as the model
    calls them), then the doctrine guards (a search door that closed
    mid-run means retry; tools meant to ground the answer produced no
    evidence: writing from model memory is exactly the fabrication
    path, so even a validated answer is discarded). No salvage
    anywhere: no validated answer is SIGNAL. And no SPEND on a
    decidable blank: tools toggled with every door closed can never
    produce evidence, so that guard fires BEFORE a completion is
    bought."""
    tools = build_tools(config)
    if config.uses_tools and not tools:
        logger.info("cell: tools toggled but no door open; writing nothing without spending")
        deps.blank_cause = StoredCellState.NO_TOOLS_DOOR
        return None
    answer = answerer.answer(prompt, tools, deps)
    if deps.door_closed:
        # A search door rate-limited this run past its backoff: the
        # row is NOT done. Whatever the model answered was built on
        # the residue of a throttled run (exactly the guess this guard
        # exists for), so it is discarded and the row is retried, the
        # same way a model-side rate limit parks it. Outranks the
        # evidence guard: a closed door is a retry, never a diagnosis.
        logger.info("cell: %s door closed (rate limited); the row will be retried", deps.door_closed)
        deps.blank_cause = THROTTLED_STATE_BY_TOOL[AgentTool(deps.door_closed)]
        return None
    if config.uses_tools and not deps.evidence:
        logger.info("cell: tools enabled but no evidence; writing nothing")
        # The answerer's own cause (a transient, a validation miss)
        # outranks the doctrine's: retrying is righter than diagnosing.
        deps.blank_cause = deps.blank_cause or StoredCellState.NO_EVIDENCE
        return None
    return answer


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
        return CellRun({}, [], [], StoredCellState.NO_EVIDENCE, StoredCellState.NO_EVIDENCE, {})
    # Raises ModelUnavailable on an unrunnable address: a config error
    # fails the run loudly; only per-row hazards degrade to blanks. A
    # caller that already resolved the address (the test POST's 400
    # gate) passes its model instead of resolving twice.
    if model is None:
        model = model_for(config.provider, config.source, config.model)
    # A caller-built deps carries the caller's doors and callbacks (the
    # fill worker's Standard search client and lease renewal); the
    # prompt is stamped here either way, one construction path.
    deps = deps if deps is not None else CellDeps()
    deps.prompt = prompt
    answer = _answer(config, prompt, CellAnswerer(model, config.outputs), deps)
    # Validation, stripping, clamping, grounding, and provenance
    # verification all happened inside the framework run; what's left
    # is keeping non-blank OUTPUT values (the companion confidence
    # fields travel via deps.assessments, never as cells).
    dump = answer.model_dump() if answer is not None else {}
    cells = {output.key: dump[output.key] for output in config.outputs if dump.get(output.key)}
    # An empty-cells run without a recorded cause: verification drops
    # diagnose UNVERIFIED (an answer arrived; nothing confirmed it for
    # this row); otherwise it is a validated answer whose every value
    # was blank, the model honestly declining, which reads NO_EVIDENCE.
    declined = deps.blank_cause or (
        StoredCellState.UNVERIFIED if deps.verification_dropped else StoredCellState.NO_EVIDENCE
    )
    cause = "" if cells else declined
    logger.info("cell: %d evidence hits -> outputs %s", len(deps.evidence), sorted(cells))
    return CellRun(
        cells,
        deps.evidence,
        deps.outcomes,
        cause,
        declined,
        # NOT filtered to the landed cells: a dropped answer is the
        # case an audit trail exists for.
        deps.assessments,
    )

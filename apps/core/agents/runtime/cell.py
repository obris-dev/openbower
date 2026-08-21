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

from openbower_schema.agents import AgentConfig

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


def _answer(config: AgentConfig, prompt: str, answerer: CellAnswerer, deps: CellDeps):
    """The validated answer, or None for blank cells: ONE call (tools
    are a parameter; the run fills deps through RunContext as the model
    calls them), ONE doctrine guard (tools meant to ground the answer
    produced no evidence: writing from model memory is exactly the
    fabrication path, so even a validated answer is discarded). No
    salvage anywhere: no validated answer is SIGNAL. And no SPEND on a
    decidable blank: tools toggled with every door closed can never
    produce evidence, so the guard fires BEFORE a completion is
    bought."""
    tools = build_tools(config)
    if config.uses_tools and not tools:
        logger.info("cell: tools toggled but no door open; writing nothing without spending")
        return None
    answer = answerer.answer(prompt, tools, deps)
    if config.uses_tools and not deps.evidence:
        logger.info("cell: tools enabled but no evidence; writing nothing")
        return None
    return answer


def run_cell(config: AgentConfig, row_data: dict, *, model: Model | None = None) -> CellRun:
    """One row's walk. Cells are keyed by the config's OWN output keys:
    the runtime speaks config-local names, and mapping them onto a
    sheet's row-data keys is the fill job's concern (phase 5), not the
    runtime's."""
    prompt = render_prompt(config.prompt, row_data).strip()
    if not prompt:
        # The one all-blank row a config can legitimately produce: every
        # {{token}} rendered empty. Diagnosed by emptiness itself.
        logger.info("cell: prompt rendered empty; writing nothing")
        return CellRun({}, [], [])
    # Raises ModelUnavailable on an unrunnable address: a config error
    # fails the run loudly; only per-row hazards degrade to blanks. A
    # caller that already resolved the address (the test POST's 400
    # gate) passes its model instead of resolving twice.
    if model is None:
        model = model_for(config.provider, config.source, config.model)
    deps = CellDeps(prompt=prompt)
    answer = _answer(config, prompt, CellAnswerer(model, config.outputs), deps)
    # Validation, stripping, clamping, and grounding all happened
    # inside the framework run; what's left is keeping non-blanks.
    cells = {key: value for key, value in answer.model_dump().items() if value} if answer is not None else {}
    logger.info("cell: %d evidence hits -> outputs %s", len(deps.evidence), sorted(cells))
    return CellRun(cells, deps.evidence, deps.outcomes)

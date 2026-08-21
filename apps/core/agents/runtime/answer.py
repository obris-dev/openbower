"""Agent construction and THE answer call, for one config: the
CellAnswerer is built from the config-derived facts (the model, the
declared outputs), so the OUTPUT TYPE and the schema-derived token cap
are constructed once at __init__. There is ONE answer method; tools
are a parameter, and no validated answer is SIGNAL (blank cells with
the diagnosis), never something to salvage with a second completion.
Validation, whitespace stripping, the cell-ceiling clamp, and URL
grounding all run INSIDE the framework (the schema and the
output-validator seam); callers get a validated answer or None."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from lists.constants import CELL_MAX_LENGTH
from openbower_schema.agents import AgentOutput

from ..constants import (
    COMPLETION_TIMEOUT_SECONDS,
    COMPLETION_TOKENS_BASE,
    COMPLETION_TOKENS_PER_OUTPUT,
    MAX_TOOL_CALLS,
    MODEL_RETRIES,
)
from ..providers import ModelUnavailable
from ..search import SearchMisconfigured
from .grounding import allowed_urls, ground_value, has_url
from .prompts import AGENT_INSTRUCTIONS, DIRECT_INSTRUCTIONS
from .tools import CellDeps

logger = logging.getLogger(__name__)

# Whitespace strips and the cell ceiling CLAMPS in the schema itself
# (a clamp, deliberately not max_length: rejection would burn a retry
# over a value we only want truncated).
_CellValue = Annotated[str, AfterValidator(lambda value: value[:CELL_MAX_LENGTH])]


def reserved_output_key(key: str) -> bool:
    """Whether a derived output key collides with the answer model's
    own attributes. Lives HERE, beside the create_model call it
    protects: a collision is either a loud construction error or, for
    model_config, a SILENT field drop (a permanently blank column).
    The serializer refuses these at the boundary, and the contract
    document ships the public set to the web's readiness mirror."""
    return hasattr(BaseModel, key)


def _ground(ctx: RunContext[CellDeps], output: BaseModel) -> BaseModel:
    """URL grounding on the framework's output-validator seam: every
    URL in every string field, EMBEDDED ones included, rewrites to its
    source form or goes (per-field blank-over-garbage, never a
    whole-answer retry). The allowed pool is the tools' evidence plus
    the rendered prompt's own URLs, both on the dependency channel."""
    allowed = allowed_urls(ctx.deps.urls, ctx.deps.prompt)
    for name in type(output).model_fields:
        value = getattr(output, name)
        if isinstance(value, str) and has_url(value):
            grounded = ground_value(value, allowed)
            if grounded != value:
                logger.info("cell: grounded %r: %s -> %s", name, value[:120], grounded[:120])
            # setattr deliberately bypasses the field validators
            # (validate_assignment is off): replacements are evidence's
            # own strings or "", already stripped and within bounds.
            setattr(output, name, grounded)
    return output


class CellAnswerer:
    """One config's answering machinery: the declared outputs AS a
    Pydantic model (each field named and described to the model), built
    once here; the token cap is schema-derived (a cap is a maximum, not
    a target)."""

    def __init__(self, model: Model, outputs: list[AgentOutput]) -> None:
        self._model = model
        fields = {o.key: (_CellValue, Field(default="", description=o.description or o.label)) for o in outputs}
        # protected_namespaces=() lets legitimate model_* output keys
        # (model_name) exist without warnings; the truly RESERVED names
        # (model_config would be silently swallowed by pydantic) are
        # refused at the serializer boundary.
        self._answer_type: type[BaseModel] = create_model(
            "CellAnswer",
            __config__=ConfigDict(str_strip_whitespace=True, protected_namespaces=()),
            **fields,
        )
        self._max_tokens = COMPLETION_TOKENS_BASE + COMPLETION_TOKENS_PER_OUTPUT * len(outputs)

    def answer(self, prompt: str, tools: list[Tool], deps: CellDeps) -> BaseModel | None:
        """THE answer call, tools or not (a parameter, never a second
        call site): the model drives its tools inside the budget and
        replies in the output type. None means no validated answer,
        which is SIGNAL (bad model fit, budget too tight for the ask),
        surfaced as blank cells plus the searches diagnosis. Auth and
        unknown-model errors are CONFIG tier and re-raise loudly: a
        revoked key fails every row identically and must never read as
        a quietly bad agent."""
        agent = self._agent(instructions=AGENT_INSTRUCTIONS if tools else DIRECT_INSTRUCTIONS, tools=tools)
        limits = UsageLimits(request_limit=MAX_TOOL_CALLS + 3, tool_calls_limit=MAX_TOOL_CALLS)

        async def run():
            # One event loop and one provider client per run, both
            # CLOSED here rather than abandoned to the garbage
            # collector (a fill would otherwise leak one pooled client
            # per row).
            try:
                result = await agent.run(f"Task:\n{prompt}", deps=deps, usage_limits=limits)
                return result.output
            finally:
                # Best-effort: scripted test models own no HTTP client.
                client = getattr(self._model, "client", None)
                if client is not None:
                    await client.close()

        try:
            return asyncio.run(run())
        except UsageLimitExceeded:
            logger.info("cell: request/tool budget exhausted before an answer")
            return None
        except SearchMisconfigured:
            # Config tier: build_tools' availability gates make this
            # unreachable, but if a gate ever regresses the error must
            # surface loudly, not blank one cell quietly.
            raise
        except ModelHTTPError as e:
            if e.status_code in (401, 403, 404):
                raise ModelUnavailable(f"the model endpoint refused the address ({e.status_code})") from e
            logger.warning("cell: answer failed (%s %s)", type(e).__name__, e.status_code)
            return None
        except Exception as e:
            logger.warning("cell: answer failed (%s): %s", type(e).__name__, e)
            return None

    def _agent(self, *, instructions: str, tools: list[Tool] | None = None) -> Agent:
        """The ONE constructor both legs share: deps-typed and grounded
        at construction, never wrapped after."""
        agent = Agent(
            self._model,
            deps_type=CellDeps,
            output_type=self._answer_type,
            tools=tools or [],
            instructions=instructions,
            model_settings=ModelSettings(max_tokens=self._max_tokens, timeout=COMPLETION_TIMEOUT_SECONDS),
            retries=MODEL_RETRIES,
        )
        agent.output_validator(_ground)
        return agent

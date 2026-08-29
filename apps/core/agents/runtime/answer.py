"""Agent construction and THE answer call, for one config: the
CellAnswerer is built from the config-derived facts (the model, the
declared outputs), so the OUTPUT TYPE and the schema-derived token cap
are constructed once at __init__. There is ONE answer method; tools
are a parameter, and no validated answer is SIGNAL (blank cells with
the diagnosis). The one second completion is the capped run's verdict
call: the tool budget caps the SPEND, not the verdict, so a model
still searching when the cap lands is asked once, tools withheld, to
judge the records it pooled, under the same floor and grounding.
Validation, whitespace stripping, the cell-ceiling clamp, and URL
grounding all run INSIDE the framework (the schema and the
output-validator seam); callers get an Answered: the validated output
or None, the cause when it is None, and the validator's judgement.
Tool state stays on deps; the answerer's verdict never does."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from lists.cell_types import shape_hint
from lists.constants import CELL_MAX_LENGTH, StoredCellState
from openbower_schema.agents import (
    CONFIDENCE_REASON_SUFFIX,
    CONFIDENCE_SUFFIX,
    RESERVED_OUTPUT_MARKER,
    AgentOutput,
)

from ..constants import (
    ANSWER_CONFIDENCE_FLOOR,
    COMPLETION_TIMEOUT_SECONDS,
    COMPLETION_TOKENS_BASE,
    COMPLETION_TOKENS_PER_OUTPUT,
    MAX_TOOL_CALLS,
    MODEL_RETRIES,
)
from ..providers import MODEL_TIMEOUT_EXCEPTIONS, ModelUnavailable
from .grounding import allowed_urls, ground_value, has_url
from .judgement import AnswerJudgement
from .prompts import AGENT_INSTRUCTIONS, CAPPED_INSTRUCTIONS, DIRECT_INSTRUCTIONS
from .tools import CellDeps


def _capped_task(prompt: str, deps: CellDeps) -> str:
    """The verdict call's task: the original ask plus the pooled
    records, as JSON so each record's own text is data inside a
    string, never structure (the same shape the tools return them
    in, so the model reads what it already read)."""
    records = json.dumps([record.as_json() for record in deps.records], ensure_ascii=False)
    return f"Task:\n{prompt}\n\nRecords gathered (your search budget is spent):\n{records}"


logger = logging.getLogger(__name__)

# Whitespace strips and the cell ceiling CLAMPS in the schema itself
# (a clamp, deliberately not max_length: rejection would burn a retry
# over a value we only want truncated).
_CellValue = Annotated[str, AfterValidator(lambda value: value[:CELL_MAX_LENGTH])]
# Confidence CLAMPS into [0, 1] (authored input clamps, never rejects:
# a rejection would burn a retry over a value we only want bounded).
_Confidence = Annotated[float, AfterValidator(lambda value: 0.0 if value != value else min(max(value, 0.0), 1.0))]


def _describe(output: AgentOutput) -> str:
    """What the model is told one output is: the author's description
    (or the label, when they wrote none) plus the SHAPE its column
    type demands. Without the shape, a date column refuses "March
    2019" as a type mismatch and SETTLES the cell, having never asked
    for YYYY-MM-DD."""
    return " ".join(part for part in (output.description or output.label, shape_hint(output.type)) if part)


class _Companions(NamedTuple):
    """One output's companion field NAMES, namespaced under the
    reserved marker so a user's own "Confidence" output never
    collides. Built through this in BOTH directions, so the schema
    that declares them and the validator that reads them back can
    never spell them differently."""

    reason: str
    confidence: str


def companions(key: str) -> _Companions:
    return _Companions(reason=f"{key}{CONFIDENCE_REASON_SUFFIX}", confidence=f"{key}{CONFIDENCE_SUFFIX}")


def reserved_output_key(key: str) -> bool:
    """Whether a derived output key collides with the answer model's
    own attributes, or wears the citation companions' suffix (a user
    output named `x_sources` beside `x` would collide with x's
    companion field). Lives HERE, beside the create_model call it
    protects: a collision is either a loud construction error or, for
    model_config, a SILENT field drop (a permanently blank column).
    The serializer refuses these at the boundary; the contract
    document ships the name set AND the marker to the web's readiness
    mirror."""
    return hasattr(BaseModel, key) or RESERVED_OUTPUT_MARKER in key


def _verify(ctx: RunContext[CellDeps], output: BaseModel, keys: list[str]) -> BaseModel:
    """The one check the runtime makes on an answer, on the same seam
    as grounding: the model's own stated confidence has to clear the
    floor, per field, blanking over garbage rather than retrying. A
    guess and a fact carry identical weight in a spreadsheet cell, so
    an answer the model would not stake the row on does not go in it.

    Nothing here reads the VALUE. Whether an answer fits the ask is
    judgment, and judgment is what an AI column is bought for; every
    mechanical stand-in for it (name shapes, identity ties, matching
    the answer against the evidence text) either encodes one vertical
    into a general feature or ends in string matching against a
    generator whose output is fuzzy by nature. The model gets its
    evidence in a known format and states how sure it is and why; the
    score gates the cell and the reason travels with it for the human
    who has to trust or override it.

    Runs for EVERY config: a knowledge column has no evidence at all
    behind it, which makes the floor matter more there, not less."""
    deps = ctx.deps
    for key in keys:
        # Every output field is a str defaulting to "" (the schema is
        # built that way and a non-str never survives validation), so
        # the only case here is the model declining to answer.
        value = getattr(output, key)
        if not value:
            continue
        # No cast and no getattr default: the schema types and defaults
        # every field (str "" for the answer and reason, float 0.0 for
        # the score) and a wrong type never survives validation.
        names = companions(key)
        confidence = getattr(output, names.confidence)
        assessment = {"confidence": confidence, "reason": getattr(output, names.reason)}
        # A floor to CLEAR, so a NaN (which compares False against
        # everything, including its own clamp) fails closed.
        if not confidence >= ANSWER_CONFIDENCE_FLOOR:
            # The discarded answer is KEPT beside its score. A log line
            # is not an audit trail: it truncates, it is not queryable
            # per row, and it is gone on the next restart. Tuning the
            # floor needs the distribution of what it rejected, and a
            # user asking why a cell is blank is better served by "the
            # model said X at 0.62, because Y" than by silence.
            assessment["dropped"] = value
            setattr(output, key, "")
            deps.judgement.verification_dropped = True
        deps.judgement.assessments[key] = assessment
        logger.info("cell: %r at %.2f%s", key, confidence, " DROPPED" if "dropped" in assessment else "")
    return output


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


class Answered(NamedTuple):
    """What one answer call decided: the validated output (None for a
    blank), WHY it is blank when it is (a StoredCellState value, ""
    otherwise: the retry causes park the row, the rest are terminal),
    and the validator's judgement of what the model said. Handed back
    to the caller; nothing on deps carries the answerer's verdict."""

    output: BaseModel | None
    cause: str
    judgement: AnswerJudgement


class CellAnswerer:
    """One config's answering machinery: the declared outputs AS a
    Pydantic model (each field named and described to the model), built
    once here; the token cap is schema-derived (a cap is a maximum, not
    a target). Every output is a TRIPLE: the answer, the model's
    account of why it is about to score it as it does, and the score
    the runtime gates on. The account travels with the cell for the
    human who has to trust or override it."""

    def __init__(self, model: Model, outputs: list[AgentOutput]) -> None:
        self._model = model
        self._keys = [o.key for o in outputs]
        # REQUIRED, every one, no defaults: a schema whose properties
        # are all required is the one pydantic-ai reports as
        # strict-compatible, and strict is what makes a provider
        # CONSTRAIN generation to the shape instead of hoping for it
        # (models/__init__.py resolves strict=None to that flag, per
        # provider). An unknown answer is the empty STRING, which the
        # instructions ask for; silence is not an option the model has.
        fields: dict = {}
        # One INTERLEAVED triple per output, in this order on purpose.
        # Structured output is generated field by field, so answering
        # then justifying then scoring puts the model's own account of
        # the evidence between the value and the number it gates on,
        # instead of a score stated cold and defended afterwards.
        # Grouping by output rather than by kind keeps each account
        # next to the answer it is about, not after every other one.
        for output in outputs:
            names = companions(output.key)
            fields[output.key] = (_CellValue, Field(description=_describe(output)))
            fields[names.reason] = (
                _CellValue,
                Field(
                    description=f"Before you score {output.key}: explain what in the evidence supports"
                    " your answer, and what you could not confirm, inferred rather than read, or found"
                    " ambiguous or out of date. Give both halves; 'nothing' is a valid second half.",
                ),
            )
            fields[names.confidence] = (
                _Confidence,
                Field(
                    description=f"Your confidence that {output.key} is correct for THIS subject. 1 means"
                    " certain, stated outright by the evidence; 0 means nothing supported it. Score a"
                    " shaky answer low rather than leaving it out.",
                ),
            )
        # protected_namespaces=() lets legitimate model_* output keys
        # (model_name) exist without warnings; the truly RESERVED names
        # (model_config would be silently swallowed by pydantic) are
        # refused at the serializer boundary.
        self._answer_type: type[BaseModel] = create_model(
            "CellAnswer",
            __config__=ConfigDict(str_strip_whitespace=True, protected_namespaces=()),
            **fields,
        )
        self._max_tokens = COMPLETION_TOKENS_BASE + COMPLETION_TOKENS_PER_OUTPUT * len(fields)

    def answer(self, prompt: str, tools: list[Tool], deps: CellDeps) -> Answered:
        """THE answer call, tools or not (a parameter, never a second
        call site): the model drives its tools inside the budget and
        replies in the output type. An Answered with no output is no
        validated answer, which is SIGNAL (bad model fit, budget too
        tight for the ask), surfaced as blank cells plus the searches
        diagnosis, and its `cause` says which. Auth and unknown-model
        errors are CONFIG tier and re-raise loudly: a revoked key fails
        every row identically and must never read as a quietly bad
        agent."""
        agent = self._agent(instructions=AGENT_INSTRUCTIONS if tools else DIRECT_INSTRUCTIONS, tools=tools)
        limits = UsageLimits(request_limit=MAX_TOOL_CALLS + 3, tool_calls_limit=MAX_TOOL_CALLS)

        async def run():
            # One event loop and one provider client per run, both
            # CLOSED here rather than abandoned to the garbage
            # collector (a fill would otherwise leak one pooled client
            # per row).
            try:
                try:
                    result = await agent.run(f"Task:\n{prompt}", deps=deps, usage_limits=limits)
                except UsageLimitExceeded:
                    # The budget ran out while the model was still
                    # reaching for a tool, so it was never asked for a
                    # verdict. With records in the pool, ask ONCE, tools
                    # withheld: the confidence floor and grounding then
                    # judge what it gathered, instead of the cap deciding
                    # the cell blank. With nothing pooled there is
                    # nothing to judge from, and the cap stands.
                    if not deps.records:
                        raise
                    logger.info(
                        "cell: tool budget spent mid-search; asking for a verdict from %d records", len(deps.records)
                    )
                    verdict = self._agent(instructions=CAPPED_INSTRUCTIONS)
                    result = await verdict.run(
                        _capped_task(prompt, deps),
                        deps=deps,
                        usage_limits=UsageLimits(request_limit=1 + MODEL_RETRIES),
                    )
                return result.output
            finally:
                # Best-effort: scripted test models own no HTTP client.
                client = getattr(self._model, "client", None)
                if client is not None:
                    await client.close()

        def blank(cause: StoredCellState) -> Answered:
            return Answered(None, cause, deps.judgement)

        try:
            return Answered(asyncio.run(run()), "", deps.judgement)
        except UsageLimitExceeded:
            # Its own cause, distinct from MODEL_ERROR: the budget spent
            # with nothing gathered to judge from is the model's verdict
            # under this config, so the blank is settled, not
            # infrastructure.
            logger.info("cell: request/tool budget exhausted with no records to answer from")
            return blank(StoredCellState.NO_ANSWER)
        except ModelHTTPError as e:
            if e.status_code in (401, 403, 404):
                raise ModelUnavailable(f"the model endpoint refused the address ({e.status_code})") from e
            # 429 and 5xx are the INFRASTRUCTURE tier: the row retries
            # (and AIMD halves); anything else is the model's own error.
            transient = e.status_code == 429 or e.status_code >= 500
            logger.warning("cell: answer failed (%s %s)", type(e).__name__, e.status_code)
            return blank(StoredCellState.TRANSIENT if transient else StoredCellState.MODEL_ERROR)
        except MODEL_TIMEOUT_EXCEPTIONS as e:
            # A timeout is indistinguishable from an overloaded server:
            # infrastructure tier, retried like a 429.
            #
            # The tuple comes from the DOORS, not from httpx alone.
            # Each SDK catches the transport's timeout and re-raises
            # its own, which does not inherit from it, so httpx's type
            # alone matches nothing a real door can raise.
            logger.warning("cell: answer timed out (%s)", type(e).__name__)
            return blank(StoredCellState.TRANSIENT)
        except UnexpectedModelBehavior as e:
            # The framework's validation retry ran dry: the model spoke,
            # but never in the output type.
            logger.warning("cell: answer failed validation: %s", e)
            return blank(StoredCellState.UNPARSEABLE)
        except Exception as e:
            logger.warning("cell: answer failed (%s): %s", type(e).__name__, e)
            return blank(StoredCellState.MODEL_ERROR)

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
        # Provenance BEFORE grounding: verification judges the model's
        # own strings; grounding may rewrite them after. Attached for
        # every config, tools or not, since the confidence floor is not
        # a tool-only rule.
        keys = self._keys

        def validate(ctx: RunContext[CellDeps], output: BaseModel) -> BaseModel:
            return _ground(ctx, _verify(ctx, output, keys))

        agent.output_validator(validate)
        return agent

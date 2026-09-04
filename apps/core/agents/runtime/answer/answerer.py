"""THE whole answer call, for one config. The CellAnswerer is built
from the config-derived facts (the model resolved from the config's
address, the declared outputs as the answer schema), so the output
type and the schema-derived token cap are constructed once at
__init__. One answer method takes the ROW: it renders the prompt,
builds the run's deps, seeds and builds the tools the toggles and
availabilities allow, and holds the doctrine guards, so no caller can perform
the sequence differently.

The exits speak the answerer's OWN vocabulary and never the sheet's:
a completed call returns an Answer (cells, which may honestly be
empty, plus the CallRecord of facts); a call that failed raises the
AgentError family, the record riding every raise; a call that cannot
happen raises its refusal (EmptyRender, NoAvailableTools). Assigning sheet
meaning to any of it is the caller's job. Config-tier trouble
(ModelUnavailable: a revoked key, a vanished address) re-raises
loudly, since it fails every row identically and must never read as a
quietly bad agent.

The one second completion is the capped run's verdict call: the tool
budget caps the SPEND, not the verdict, so a model still searching
when the cap lands is asked once, tools withheld, to judge the
records it pooled, under the same floor and grounding. Validation,
whitespace stripping, the cell-ceiling clamp, and URL grounding all
run INSIDE the framework (the schema and the output-validator seam);
deps lives and dies inside answer(): the record is its projection."""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from openbower_schema.agents import AgentConfig

from ...constants import (
    COMPLETION_TIMEOUT_SECONDS,
    COMPLETION_TOKENS_BASE,
    COMPLETION_TOKENS_PER_OUTPUT,
    MAX_TOOL_CALLS,
    MODEL_RETRIES,
    ToolStatus,
)
from ...providers import ModelUnavailable, model_for, model_timeout_exceptions
from ...tools import registry as tool_registry
from ...tools.base import FailureMode
from ..deps import CellDeps
from ..prompts import AGENT_INSTRUCTIONS, CAPPED_INSTRUCTIONS, DIRECT_INSTRUCTIONS, render_prompt
from .errors import (
    AgentError,
    AgentOverloaded,
    AgentRateLimited,
    AgentResponseInvalid,
    AgentTimeout,
    AgentUnableToRespond,
    AgentUnreachable,
    EmptyRender,
    NoAvailableTools,
)
from .record import Answer, CallRecord
from .schema import construct_answer_schema, deconstruct_answer
from .validators import ground, verify

logger = logging.getLogger(__name__)


def _capped_task(prompt: str, deps: CellDeps) -> str:
    """The verdict call's task: the original ask plus the pooled
    records, as JSON so each record's own text is data inside a
    string, never structure (the same shape the tools return them
    in, so the model reads what it already read)."""
    records = json.dumps([record.as_json() for record in deps.records], ensure_ascii=False)
    return f"Task:\n{prompt}\n\nRecords gathered (your search budget is spent):\n{records}"


class CellAnswerer:
    """One config's answering machinery. Construction RESOLVES: the
    config is the ONE source of the model (a caller validating an
    address early resolves and DISCARDS, the worker's claim-time
    pattern; tests patch model_for), so an unrunnable address raises
    ModelUnavailable here, the config tier, before any spend.
    Resolution is per construction on purpose: a run closes its
    model's client, so concurrent rows must never share one."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._model: Model = model_for(config.provider, config.source, config.model)
        self._keys = [o.key for o in config.outputs]
        self._answer_type = construct_answer_schema(config.outputs)
        self._max_tokens = COMPLETION_TOKENS_BASE + COMPLETION_TOKENS_PER_OUTPUT * len(self._answer_type.model_fields)

    def _render_prompt(self, row_data: dict) -> str:
        """The row against the config's template: the call's one
        row-shaped input. Raises EmptyRender on an all-blank render,
        since a row that asked nothing has no call to make. A METHOD
        taking the row, never a cached value: the answerer is
        per-config, the prompt is per-row."""
        prompt = render_prompt(self._config.prompt, row_data).strip()
        if not prompt:
            raise EmptyRender()
        return prompt

    def _hydrate_deps(self) -> CellDeps:
        """A fresh call record, every toggled tool's status seeded
        from its availability: what the guards, the offered tools, and
        the caller's blank naming all read. Per CALL, never cached:
        availability is deploy state (credentials, the configured
        provider) read at call time, and each row's record must start
        empty."""
        deps = CellDeps()
        for tool in tool_registry.toggled_tools(self._config):
            deps.tool_status[tool.name] = tool.availability()
        return deps

    def _tool_failure(self, deps: CellDeps) -> tuple[str, FailureMode] | None:
        """The BLAMING tool's name and failure mode, when one exists: the first
        toggled tool (registration = blame order) that failed without
        ever serving. A tool that SERVED cannot take the blame: it
        gave the model real evidence, so a decline over that evidence
        is the model's verdict, not the tool's fault, and blaming the
        tool would park the row to re-buy the same verdict. A toggled
        tool that was never even offered (not configured) DOES take
        it, deliberately: the missing tool may be exactly why the
        output is empty, and Continue re-runs it once the provider is
        set up (provider credentials live in deployment settings,
        outside the config fingerprint, so no settled state could
        re-open on setup)."""
        for tool in tool_registry.toggled_tools(self._config):
            if tool.name in deps.served:
                continue
            # Value compare against the BASE, never identity against
            # one tool's enum: another tool's own OPEN member must read
            # as open here.
            status = deps.tool_status.get(tool.name, ToolStatus.OPEN)
            if status != ToolStatus.OPEN:
                # Direct lookup, loudly: registration guarantees every
                # non-open code a failure mode, so a miss is a broken
                # contract, never a case to default around.
                return tool.name, tool.failure_modes[status]
        return None

    def _record(self, deps: CellDeps) -> CallRecord:
        """Every record leaves through here, so the blame fact is
        computed once, on every exit alike."""
        blame = self._tool_failure(deps)
        blamed_tool, tool_failure = blame if blame is not None else ("", None)
        return CallRecord.from_deps(deps, blamed_tool=blamed_tool, tool_failure=tool_failure)

    def _build_tools(self, deps: CellDeps) -> list[Tool]:
        """The tools the toggles and availabilities allow, or the
        NoAvailableTools refusal when tools were asked for and none is
        available (the no-spend guard: a call that cannot produce
        evidence must not buy a completion). A config with no tools
        toggled builds an empty list and proceeds: that is the direct,
        knowledge-only call."""
        tools = tool_registry.build_tools(self._config, deps)
        if self._config.uses_tools and not tools:
            raise NoAvailableTools(self._record(deps))
        return tools

    def answer(self, row_data: dict) -> Answer:
        """THE call: render, hydrate, build, run, project. Returns an
        Answer for every COMPLETED call, an honest all-blank verdict
        included; raises the AgentError family (facts riding every
        raise) for a call that failed, and the refusals for a call
        that cannot happen. See the module docstring for the whole
        contract.

        The one guard on a call that DID complete is no salvage: tools
        toggled with zero evidence pooled means even validated cells
        are discarded, since writing from model memory is exactly the
        fabrication path. The why is on the tools: a tool that never
        answered is a retry, a tool that answered nothing is a
        diagnosis."""
        prompt = self._render_prompt(row_data)
        deps = self._hydrate_deps()
        tools = self._build_tools(deps)
        agent = self._agent(prompt, instructions=AGENT_INSTRUCTIONS if tools else DIRECT_INSTRUCTIONS, tools=tools)
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
                    verdict = self._agent(prompt, instructions=CAPPED_INSTRUCTIONS)
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

        # The framework's failure surface converts to OUR vocabulary at
        # this one boundary: each leg re-raises a typed AgentError with
        # the record aboard. Raising from an except clause propagates
        # past the remaining legs, so the blanket catch cannot swallow
        # the conversions.
        try:
            output = asyncio.run(run())
        except UsageLimitExceeded as e:
            # Its own type, distinct from the unknown-failure base: the
            # budget spent with nothing gathered to judge from is the
            # model's verdict under this config (settled), not
            # infrastructure. Reached with nothing pooled, and also
            # when the capped verdict's own budget ran dry: either way
            # no verdict landed.
            logger.info("cell: request/tool budget exhausted before a verdict could land")
            raise AgentUnableToRespond(self._record(deps)) from e
        except ModelHTTPError as e:
            if e.status_code in (401, 403, 404):
                raise ModelUnavailable(f"the model endpoint refused the address ({e.status_code})") from e
            logger.warning("cell: answer failed (%s %s)", type(e).__name__, e.status_code)
            record = self._record(deps)
            # 429 and 5xx are the INFRASTRUCTURE tier (retriable);
            # anything else is the model's own error, unknown and
            # therefore fatal.
            if e.status_code == 429:
                raise AgentRateLimited(record) from e
            if e.status_code >= 500:
                raise AgentOverloaded(record) from e
            raise AgentError(record, f"the model endpoint answered {e.status_code}") from e
        except ModelAPIError as e:
            # The framework wraps the SDKs' WHOLE transport family in
            # this one type (connection refused, DNS, dropped sockets,
            # and the SDK timeouts, which subclass their connection
            # error), so this is the could-not-complete-the-call tier:
            # retriable, because the model never spoke. The cause tells
            # a timeout from an unreachable server for the record.
            logger.warning("cell: model transport failed (%s: %s)", type(e.__cause__ or e).__name__, e.message)
            record = self._record(deps)
            if isinstance(e.__cause__, model_timeout_exceptions()):
                raise AgentTimeout(record) from e
            raise AgentUnreachable(record) from e
        except model_timeout_exceptions() as e:
            # The tuple comes from the PROVIDERS, not from httpx alone
            # (each SDK re-raises the transport's timeout as its own
            # type). A wrapped path arrives as ModelAPIError above;
            # this arm catches the SAME failure raised bare, so a path
            # outside the framework's wrapper cannot fall through to
            # the fatal tier.
            logger.warning("cell: answer timed out (%s)", type(e).__name__)
            raise AgentTimeout(self._record(deps)) from e
        except UnexpectedModelBehavior as e:
            logger.warning("cell: answer failed validation: %s", e)
            raise AgentResponseInvalid(self._record(deps)) from e
        except ModelUnavailable:
            raise
        except Exception as e:
            logger.warning("cell: answer failed (%s): %s", type(e).__name__, e)
            raise AgentError(self._record(deps), f"{type(e).__name__}: {e}") from e

        cells = deconstruct_answer(output, self._keys)
        if self._config.uses_tools and not deps.records:
            logger.info("cell: tools enabled but no evidence; writing nothing")
            cells = {}
        return Answer(cells, self._record(deps))

    def _agent(self, prompt: str, *, instructions: str, tools: list[Tool] | None = None) -> Agent:
        """The ONE constructor both legs share: deps-typed and grounded
        at construction, never wrapped after. `prompt` is the rendered
        task, closed over by the validator: its URLs are the user's own
        ground truth, so grounding allows them beside the tools' hits."""
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
            return ground(ctx, verify(ctx, output, keys), prompt)

        agent.output_validator(validate)
        return agent

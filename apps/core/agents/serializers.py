"""Request validation + wire builders for the agents domain. Wire
builders CONSTRUCT the contract models: drift between the contract and
the views fails loudly here, never in the client's zod."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.template.exceptions import TemplateSyntaxError
from django.utils import timezone
from rest_framework import serializers

from lists.constants import ColumnType
from openbower_kernel.fields import min_ulid_at
from openbower_schema.agents import AgentListItem
from openbower_schema.agents import AgentSummary as WireAgentSummary
from openbower_schema.agents import AgentTestRun as WireTestRun

from .constants import (
    LABEL_MAX_LENGTH,
    MAX_AGENT_OUTPUTS,
    MODEL_MAX_LENGTH,
    OUTPUT_DESCRIPTION_MAX_LENGTH,
    OUTPUT_KEY_MAX_LENGTH,
    OUTPUT_LABEL_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_RUN_STALE_PENDING_SECONDS,
    TEST_RUN_WORST_CASE_SECONDS,
    TEST_VALUE_MAX_LENGTH,
    AgentProvider,
    AgentTool,
    TestRunStatus,
)
from .models import Agent, AgentTestRun


class OutputDef(serializers.Serializer):
    key = serializers.CharField(required=False, allow_blank=True, max_length=OUTPUT_KEY_MAX_LENGTH)
    label = serializers.CharField(max_length=OUTPUT_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[t.value for t in ColumnType])
    description = serializers.CharField(required=False, allow_blank=True, max_length=OUTPUT_DESCRIPTION_MAX_LENGTH)


def _clean_outputs(outputs: list[dict]) -> list[dict]:
    """Keys derive from labels when absent; distinct keys required
    (cells key on them, collisions would silently merge columns)."""
    from openbower_schema.lists import derive_column_key

    from .runtime.answer import reserved_output_key

    cleaned = []
    for output in outputs:
        # DRF's CharField already trimmed and refused blank labels.
        label = output["label"]
        key = derive_column_key(label, key=output.get("key") or "")
        if not key:
            raise serializers.ValidationError("each output needs a key (or a label to derive one)")
        if reserved_output_key(key):
            # The runtime builds a pydantic model from these keys, and a
            # BaseModel-attribute collision is either a loud error or,
            # for model_config, a SILENT drop (a permanently blank
            # column with zero diagnosis). The message speaks the
            # LABEL the user typed, not the derived key.
            raise serializers.ValidationError(f"{label!r} maps to a reserved output key; pick a different name")
        cleaned.append(
            {
                "key": key[:OUTPUT_KEY_MAX_LENGTH],
                "label": label,
                "type": output["type"],
                "description": (output.get("description") or "").strip(),
            }
        )
    if len({o["key"] for o in cleaned}) != len(cleaned):
        raise serializers.ValidationError("output keys must be distinct")
    return cleaned


class AgentConfigRequest(serializers.Serializer):
    """The config shape both custodies and the test bench share."""

    prompt = serializers.CharField(max_length=PROMPT_MAX_LENGTH)
    provider = serializers.ChoiceField(choices=[p.value for p in AgentProvider])
    source = serializers.CharField(max_length=SOURCE_MAX_LENGTH)
    model = serializers.CharField(max_length=MODEL_MAX_LENGTH)
    tools = serializers.DictField(child=serializers.BooleanField(), required=False, default=dict)
    outputs = OutputDef(many=True, min_length=1, max_length=MAX_AGENT_OUTPUTS)

    def validate_prompt(self, value: str) -> str:
        # Template SYNTAX is config: a prompt the engine cannot parse
        # refuses here, never as a per-row surprise.
        from .runtime.prompts import validate_prompt

        try:
            validate_prompt(value)
        except TemplateSyntaxError as e:
            raise serializers.ValidationError(f"prompt template error: {e}") from e
        return value

    def validate_tools(self, value: dict) -> dict:
        unknown = set(value) - {t.value for t in AgentTool}
        if unknown:
            # A typo'd tool name silently vanishing would run the agent
            # without the tool the user thinks is on.
            raise serializers.ValidationError(f"unknown tool(s): {', '.join(sorted(unknown))}")
        return {t.value: bool(value.get(t.value)) for t in AgentTool}

    def validate_outputs(self, value: list[dict]) -> list[dict]:
        return _clean_outputs(value)


class AgentCreateRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    config = AgentConfigRequest()


class AgentPatchRequest(serializers.Serializer):
    """Any subset: label alone (a rename) or config alone; at least
    one field must be present (the lists idiom)."""

    label = serializers.CharField(max_length=LABEL_MAX_LENGTH, required=False)
    config = AgentConfigRequest(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("nothing to change")
        return attrs


def agent_wire(agent: Agent) -> dict[str, Any]:
    return WireAgentSummary(
        id=str(agent.id),
        label=agent.label[:LABEL_MAX_LENGTH],
        config=agent.config(),
        ephemeral=agent.ephemeral,
        created_at=agent.created_at.isoformat(),
        updated_at=agent.updated_at.isoformat(),
    ).model_dump()


def test_run_wire(run: AgentTestRun) -> dict[str, Any]:
    """The polled envelope. A pending run older than the stale window
    presents as FAILED with its why (daemon threads die unwound on
    restarts and nothing else revisits a pending row); presentation
    only, no write (a zombie completion may still land). Staleness is
    judged off the ULID id's time prefix, the SAME fact the start
    guard filters on (two legs judging different columns can
    disagree)."""
    status = run.status
    error = run.error or None
    if status == TestRunStatus.PENDING and str(run.id) < min_ulid_at(
        timezone.now() - timedelta(seconds=TEST_RUN_STALE_PENDING_SECONDS)
    ):
        status = TestRunStatus.FAILED
        error = "the run was interrupted (the server restarted mid-run); run the test again"
    return WireTestRun(
        id=str(run.id),
        status=status,
        # The stored shape is a CellRunResult; AgentTestResult is the
        # NARROWER bench view of it, so this is a projection and the
        # extra keys (the causes and assessments the fill's drawer
        # reads) are dropped here on purpose, by the field's type.
        #
        # `or None`: a complete row whose result is the model default
        # {} must present as the contract-breach it is, not 500 the
        # poll inside AgentTestResult validation.
        result=(run.result or None) if status == TestRunStatus.COMPLETE else None,
        error=error,
        # The client's poll budget is the runtime's WORST CASE, not
        # the stale window: a hung run must not spin the client for
        # the extra ~900s the orphan margin exists for.
        poll_budget_seconds=TEST_RUN_WORST_CASE_SECONDS,
    ).model_dump()


def list_item_wire(agent: Agent) -> dict[str, Any]:
    """The list's slim row: the index renders name, model, tools, and
    dates; shipping every agent's full config to draw four columns
    would be megabytes at the list cap."""
    return AgentListItem(
        id=str(agent.id),
        label=agent.label[:LABEL_MAX_LENGTH],
        model=agent.model,
        tools=agent.tools_wire(),
        created_at=agent.created_at.isoformat(),
        updated_at=agent.updated_at.isoformat(),
    ).model_dump()


class AgentTestRequest(serializers.Serializer):
    """POST /v1/agents/test: a drafted config plus one hand-fed row of
    {{token}} values, every authored value bounded (clamped, never
    rejected, matching the sheet's own cell doctrine)."""

    config = AgentConfigRequest()
    row = serializers.DictField(child=serializers.CharField(allow_blank=True, trim_whitespace=False), default=dict)
    # The borrowed row's id, when the bench row came from a sheet: it
    # makes the run seedable by a later fill admission (the prewrite
    # economy). Blank for hand-typed rows; never validated against a
    # sheet here (admission does that, with the sheet in hand).
    row_id = serializers.CharField(required=False, allow_blank=True, default="", max_length=26)

    def validate_row(self, value: dict) -> dict:
        # DictField(child=CharField) already guaranteed strings. The key
        # COUNT clamps like everything else here (first N in JSON
        # order): a 17-variable prompt is authored input, not an error.
        # Truncated keys keep FIRST-wins semantics: two long keys
        # sharing a prefix must not silently collapse to the later one.
        row: dict[str, str] = {}
        for key, item in list(value.items())[:TEST_ROW_MAX_KEYS]:
            row.setdefault(key[:TEST_KEY_MAX_LENGTH], item[:TEST_VALUE_MAX_LENGTH])
        return row

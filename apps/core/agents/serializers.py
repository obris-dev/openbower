"""Request validation + wire builders for the agents domain. Wire
builders CONSTRUCT the contract models: drift between the contract and
the views fails loudly here, never in the client's zod."""

from __future__ import annotations

from typing import Any

from django.template.exceptions import TemplateSyntaxError
from rest_framework import serializers

from openbower_schema.agents import AgentListItem, AgentTools
from openbower_schema.agents import AgentSummary as WireAgentSummary
from openbower_schema.lists import COLUMN_TYPE_CHOICES

from .constants import (
    LABEL_MAX_LENGTH,
    MAX_AGENT_OUTPUTS,
    MODEL_MAX_LENGTH,
    OUTPUT_DESCRIPTION_MAX_LENGTH,
    OUTPUT_KEY_MAX_LENGTH,
    OUTPUT_LABEL_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    AgentProvider,
)
from .models import Agent


class OutputDef(serializers.Serializer):
    key = serializers.CharField(required=False, allow_blank=True, max_length=OUTPUT_KEY_MAX_LENGTH)
    label = serializers.CharField(max_length=OUTPUT_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=list(COLUMN_TYPE_CHOICES))
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
        unknown = set(value) - set(AgentTools.model_fields)
        if unknown:
            # A typo'd tool name silently vanishing would run the agent
            # without the tool the user thinks is on.
            raise serializers.ValidationError(f"unknown tool(s): {', '.join(sorted(unknown))}")
        return {name: bool(value.get(name)) for name in AgentTools.model_fields}

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

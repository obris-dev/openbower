"""The answer SCHEMA and its mirror: constructed around the companions
(each declared output plus its reason and confidence fields) on the way
in, taken back off on the way out, so the schema's whole life is
bounded by the construct/deconstruct pair in this module and no caller
ever learns companion fields exist."""

from __future__ import annotations

from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from lists.cell_types import shape_hint
from lists.constants import CELL_MAX_LENGTH
from openbower_schema.agents import (
    CONFIDENCE_REASON_SUFFIX,
    CONFIDENCE_SUFFIX,
    RESERVED_OUTPUT_MARKER,
    AgentOutput,
)

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


def construct_answer_schema(outputs: list[AgentOutput]) -> type[BaseModel]:
    """The answer schema, CONSTRUCTED around the companions:
    `deconstruct_answer` is the mirror that takes them back off.

    REQUIRED, every field, no defaults: a schema whose properties are
    all required is the one pydantic-ai reports as strict-compatible,
    and strict is what makes a provider CONSTRAIN generation to the
    shape instead of hoping for it (models/__init__.py resolves
    strict=None to that flag, per provider). An unknown answer is the
    empty STRING, which the instructions ask for; silence is not an
    option the model has.

    One INTERLEAVED triple per output, in this order on purpose.
    Structured output is generated field by field, so answering then
    justifying then scoring puts the model's own account of the
    evidence between the value and the number it gates on, instead of
    a score stated cold and defended afterwards. Grouping by output
    rather than by kind keeps each account next to the answer it is
    about, not after every other one."""
    fields: dict = {}
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
    return create_model(
        "CellAnswer",
        __config__=ConfigDict(str_strip_whitespace=True, protected_namespaces=()),
        **fields,
    )


def deconstruct_answer(output: BaseModel, keys: list[str]) -> dict[str, str]:
    """`construct_answer_schema`'s mirror: the final validated answer
    with its companions taken back off, PROJECTED into cells.

    A projection AFTER the run, deliberately not another output
    validator: the validator seam is type-preserving (BaseModel in and
    out, so the verify -> ground chain composes) and can run on
    attempts a ModelRetry then discards, while this must run exactly
    once, on the survivor.

    Only the DECLARED outputs are cells, so the walk is over the
    declared keys, never over the dump: the companion fields (reason,
    confidence) ride the judgement into the stored run as the audit,
    never a column. An empty value is the model declining that output
    (the instructions ask for "" when it found nothing), so it is not
    a cell either: the column stays unanswered and takes a declined
    cause instead. Validation, stripping, clamping, grounding, and the
    confidence floor all ran inside the framework already: a value
    that scored under the floor is "" here, kept under `dropped` on
    its assessment."""
    dump = output.model_dump()
    return {key: dump[key] for key in keys if dump.get(key)}

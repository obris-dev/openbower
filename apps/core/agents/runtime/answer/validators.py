"""The output-validator chain, on the framework's seam: verification
(the confidence floor) then grounding (URL honesty), both type
preserving (BaseModel in and out) so they compose, both writing what
they learn onto the dependency channel (a validator receives only
RunContext and can return only the output, so deps is its one
mailbox)."""

from __future__ import annotations

import logging

from pydantic import BaseModel
from pydantic_ai import RunContext

from ...constants import ANSWER_CONFIDENCE_FLOOR
from ..deps import CellDeps
from ..grounding import allowed_urls, ground_value, has_url
from .schema import companions

logger = logging.getLogger(__name__)


def verify(ctx: RunContext[CellDeps], output: BaseModel, keys: list[str]) -> BaseModel:
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
        # The assessment quotes the model VERBATIM, pre-grounding, on
        # purpose: the audit shows what the model actually said,
        # fabricated URLs included. Grounding edits only what lands in
        # cells, never the record of what was claimed.
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
        logger.info("cell: %r at %.2f%s", key, confidence, " DROPPED" if assessment.get("dropped") else "")
    return output


def ground(ctx: RunContext[CellDeps], output: BaseModel, prompt: str) -> BaseModel:
    """URL grounding on the framework's output-validator seam: every
    URL in every string field, EMBEDDED ones included, rewrites to its
    source form or goes (per-field blank-over-garbage, never a
    whole-answer retry). The allowed pool is the tools' evidence plus
    the rendered prompt's own URLs, both on the dependency channel."""
    allowed = allowed_urls(ctx.deps.urls, prompt)
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

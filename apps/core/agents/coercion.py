"""Reading a STORED config back as the contract model, tolerantly.

A config is written by one version and read by later ones, so the
bounds and enums it validates against on READ are not the ones it was
written under. A read that refuses is worse than a read that renders
something clamped: the row is already on disk, the GET that touches it
has no way to skip it, and no surface exists to remove it.

One custody reads through here: the agent row. A fill's FROZEN
snapshot once did too, for the fills page that showed all history;
that page is live-only now and the snapshot's one reader is the
worker, which parses strictly, so a snapshot an old contract wrote
fails its own fill loudly (FILL_UNRUNNABLE) instead of 500ing an
endpoint permanently."""

from __future__ import annotations

import logging
from typing import Any

from lists.constants import ColumnType
from openbower_schema.agents import AgentConfig, AgentTools

from .constants import (
    MAX_AGENT_OUTPUTS,
    MODEL_MAX_LENGTH,
    OUTPUT_DESCRIPTION_MAX_LENGTH,
    OUTPUT_KEY_MAX_LENGTH,
    OUTPUT_LABEL_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    AgentProvider,
)

logger = logging.getLogger(__name__)

# What an all-junk output set renders as. A run WOULD write this
# column, and a cell under "unreadable output" is a diagnosis rather
# than data; the warning below is the trail to whatever wrote it.
UNREADABLE_OUTPUT = {"key": "unreadable_output", "label": "Unreadable output", "type": "text", "description": ""}


def _text(value: Any) -> str:
    """A stored scalar as text, with null CLAMPING to blank rather than
    rendering as the string "None"."""
    return "" if value is None else str(value)


def coerce_tools(stored: Any) -> dict[str, bool]:
    """Every declared toggle, junk storage rendering as its default."""
    values = stored if isinstance(stored, dict) else {}
    return {field: bool(values.get(field)) for field in AgentTools.model_fields}


def coerce_config(stored: dict[str, Any], *, origin: str) -> AgentConfig:
    """A stored config as the contract model: sizes CLAMP, enums fall
    back, and an empty output set gets a placeholder that names its own
    brokenness, because the contract's min-1 must hold to render at
    all.

    A retired provider renders under the FIRST spec, and normally
    refuses at run time because no same-named source exists there; a
    deploy that DOES name one identically under the substitute spec
    would run it there, which is the render-over-refuse trade this
    read path makes.

    `origin` names the row in the warning, so a coerced read is a trail
    back to whatever wrote it rather than a silent repair. The trail
    covers the OUTPUTS, the PROVIDER, the PROMPT, and a non-object
    blob; a clamped source or model is silent, because those are free
    text whose only bound is a length nothing meaningful sits near."""
    # TOTAL: the caller's rescue for a stored blob is `or {}`, which
    # only catches a FALSY one, so a truthy non-dict (a list, a string)
    # reached .get and raised AttributeError, which is the permanent
    # 500 on the fills page this whole module exists to prevent.
    if not isinstance(stored, dict):
        logger.warning("%s: stored config is not an object (%s)", origin, type(stored).__name__)
        stored = {}
    column_types = {t.value for t in ColumnType}
    providers = {p.value for p in AgentProvider}
    stored_rows = stored.get("outputs") if isinstance(stored.get("outputs"), list) else []
    rows = [o for o in stored_rows if isinstance(o, dict)][:MAX_AGENT_OUTPUTS]
    outputs = [
        {
            # _text here too, and it matters MORE than on the scalars
            # above: a key becomes a COLUMN KEY and a label becomes a
            # sheet header, and nothing downstream refuses the literal
            # "None" (AgentOutput bounds their length, not their
            # shape).
            "key": _text(o.get("key"))[:OUTPUT_KEY_MAX_LENGTH],
            "label": _text(o.get("label"))[:OUTPUT_LABEL_MAX_LENGTH],
            "type": o.get("type") if o.get("type") in column_types else ColumnType.TEXT.value,
            "description": _text(o.get("description"))[:OUTPUT_DESCRIPTION_MAX_LENGTH],
        }
        for o in rows
    ]
    if not outputs:
        outputs = [dict(UNREADABLE_OUTPUT)]
    provider = stored.get("provider")
    if provider not in providers:
        provider = AgentProvider.OPENAI_COMPATIBLE.value
    prompt = _text(stored.get("prompt"))[:PROMPT_MAX_LENGTH]
    if (
        outputs != stored_rows
        or provider != stored.get("provider")
        or len(_text(stored.get("prompt"))) > PROMPT_MAX_LENGTH
    ):
        # Loud, not silent: a clamped or coerced read means some
        # producer wrote what the contract refuses; renderable today,
        # but the log is the trail to that producer.
        logger.warning("%s: stored config clamped/coerced on read", origin)
    return AgentConfig(
        prompt=prompt,
        provider=provider,
        # `str(v) if v is not None` and not str(v): a stored null must
        # CLAMP to blank, never render as the literal "None", which
        # invents a value rather than bounding one.
        source=_text(stored.get("source"))[:SOURCE_MAX_LENGTH],
        model=_text(stored.get("model"))[:MODEL_MAX_LENGTH],
        tools=AgentTools(**coerce_tools(stored.get("tools"))),
        outputs=outputs,
    )

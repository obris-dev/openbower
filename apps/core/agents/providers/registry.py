"""The inference doors, a registry keyed by AgentProvider. Providers
are API SPECS, never companies, and each spec holds NAMED SOURCES (a
deploy runs a local Ollama and the canonical vendor side by side). A
model's full address is (provider, source, model). A source is OPEN
when its key is set or its base is non-canonical (keyless local
servers), CLOSED keyless at the canonical vendor.

`catalog_entries()` lists every runnable (provider, source, model);
`model_for(...)` answers the pydantic-ai Model for an address and
RAISES ModelUnavailable when the address cannot run (unknown provider,
unknown or closed source): an unrunnable ADDRESS is a config error
that fails every cell identically, so it fails loudly up front;
per-row hazards (searches, completions) are what degrade quietly.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic_ai.models import Model

from ..constants import CATALOG_MAX_MODELS, CATALOG_PROBE_CONCURRENCY, AgentProvider
from . import anthropic_compatible, openai_compatible
from .base import ProviderDoor

logger = logging.getLogger(__name__)

_DOORS: dict[AgentProvider, ProviderDoor] = {
    AgentProvider.OPENAI_COMPATIBLE: openai_compatible.DOOR,
    AgentProvider.ANTHROPIC_COMPATIBLE: anthropic_compatible.DOOR,
}


def catalog_entries() -> tuple[list[tuple[str, str, str]], bool]:
    """Every runnable (provider, source, model) on this deploy, doors in
    enum order, sources in env order, models as each source ranks them;
    the flag says the cap CUT the list (the wire carries it: a silent
    cap would poison the vanished-model diagnosis for addresses that
    still run, since model_for validates against the full roster).
    Probes fan out CONCURRENTLY (a cold catalog costs the slowest
    source, never the sum of timeouts) and reassemble in the
    deterministic door/source order."""
    pairs = [(provider, door, source_name) for provider, door in _DOORS.items() for source_name in door.sources()]
    if not pairs:
        return [], False
    with ThreadPoolExecutor(max_workers=min(CATALOG_PROBE_CONCURRENCY, len(pairs))) as pool:
        rosters = list(pool.map(lambda pair: pair[1].models(pair[2]), pairs))
    entries = [
        (provider.value, source_name, model)
        for (provider, _door, source_name), roster in zip(pairs, rosters, strict=True)
        for model in roster
    ]
    truncated = len(entries) > CATALOG_MAX_MODELS
    if truncated:
        logger.warning("catalog truncated to %d of %d models", CATALOG_MAX_MODELS, len(entries))
        entries = entries[:CATALOG_MAX_MODELS]
    return entries, truncated


class ModelUnavailable(Exception):
    """The address names no runnable model on this deploy."""


def model_for(provider: str, source: str, model: str) -> Model:
    # The enum parse alone decides "unknown provider": a ValueError
    # out of SDK model construction must not be misattributed to it.
    try:
        spec = AgentProvider(provider)
    except ValueError as e:
        raise ModelUnavailable(f"unknown provider in address {provider}/{source}/{model}") from e
    door = _DOORS[spec]
    runnable = door.pydantic_model(source, model)
    if runnable is None:
        raise ModelUnavailable(f"source unknown or closed for address {provider}/{source}/{model}")
    roster = door.models(source)
    if roster and model not in roster:
        # The third component of the address, checked against the
        # roster the door already probed (cached, so this is free). An
        # EMPTY roster is taken on faith rather than blocking runs on
        # /models uptime: a wrong name then fails at completion time as
        # a config-tier refusal.
        raise ModelUnavailable(f"model not on the source's roster for address {provider}/{source}/{model}")
    return runnable

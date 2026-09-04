"""The inference-provider registry: name -> InferenceProvider, with
ONE guarded write path, the same discipline as the tool registry:
each provider MODULE registers itself at its own bottom
(`register(PROVIDER)`), the roster lives in AgentsConfig.ready(),
validation is all-or-nothing BEFORE the registry mutates, a name
collision between two different providers fails loud, and
re-registering the same provider is idempotent.

No ordering rides registration: the roster is walked off the
directory (alphabetical, deterministic), and nothing semantic hangs
on provider order (the picker groups by SOURCE; the catalog lists
providers in name order).

Providers are API SPECS, never companies, and each holds NAMED
SOURCES (a deploy runs a local Ollama and the canonical vendor side
by side). A model's full address is (provider, source, model).
`model_for(...)` RAISES ModelUnavailable when the address cannot run
(unknown provider, unknown or closed source): an unrunnable ADDRESS
is a config error that fails every cell identically, so it fails
loudly up front; per-row hazards (searches, completions) are what
degrade quietly."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from pydantic_ai.models import Model

from openbower_kernel.provider_config import vendor_host

from ..constants import CATALOG_MAX_MODELS, CATALOG_PROBE_CONCURRENCY, PROVIDER_MAX_LENGTH
from .base import InferenceProvider, SourceConfig

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, InferenceProvider] = {}


class ModelUnavailable(Exception):
    """The address names no runnable model on this deploy."""


def register(provider: InferenceProvider) -> None:
    """Register one provider. Raises ValueError on an invalid
    declaration or a name collision; re-registering the same provider
    object is a no-op."""
    _validate(provider)
    existing = _REGISTRY.get(provider.name)
    if existing is not None:
        if existing is provider:
            return
        raise ValueError(f"provider name {provider.name!r} is already registered by another provider")
    _REGISTRY[provider.name] = provider


def _validate(provider: InferenceProvider) -> None:
    """The whole declaration, checked before any mutation. Each check
    names the registration it refuses, because these errors surface at
    import time where a bare assertion would read as a framework bug."""
    name = getattr(provider, "name", "")
    if not isinstance(name, str) or not name.isidentifier() or len(name) > PROVIDER_MAX_LENGTH:
        raise ValueError(f"provider name {name!r} must be a valid identifier of at most {PROVIDER_MAX_LENGTH} chars")
    base = getattr(provider, "canonical_base", "")
    if not isinstance(base, str) or not base.startswith(("http://", "https://")):
        raise ValueError(f"provider {name!r} canonical_base must start with http:// or https://, got {base!r}")
    try:
        urlsplit(base)
    except ValueError as e:
        raise ValueError(f"provider {name!r} canonical_base is not a parsable URL: {e}") from e
    # A parsable base with no HOST would compare every source
    # non-canonical, i.e. keyless-OPEN at the vendor, the exact
    # hazard the canonical flag exists to prevent.
    if not vendor_host(base):
        raise ValueError(f"provider {name!r} canonical_base {base!r} carries no host")
    timeout = getattr(provider, "timeout_exception", None)
    if not (isinstance(timeout, type) and issubclass(timeout, Exception)):
        raise ValueError(f"provider {name!r} must declare timeout_exception as an Exception subclass")


def validate_inference_sources() -> None:
    """BOTH directions of the roster's couplings, refused at boot
    (config errors fail every request identically, so they must fail
    the start, loudly). File to registry: the toml parser takes any
    section as written, so a section naming no registered provider
    refuses here, where the roster is known. Registry to wire: a
    provider that registered but never joined the wire Literal would
    otherwise 500 the catalog and the roster reads at request time
    (CatalogModel.provider and AgentConfig.provider are the closed
    Literal), so that refuses here too, naming the missing step.
    Called from AgentsConfig.ready() after the roster imports."""
    from typing import get_args

    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from openbower_schema.agents import AgentProvider

    unknown = set(settings.INFERENCE_SOURCES) - set(_REGISTRY)
    if unknown:
        raise ImproperlyConfigured(
            f"unknown provider section(s) in the providers config: {', '.join(sorted(unknown))} "
            f"(registered providers: {', '.join(_REGISTRY)})"
        )
    unwired = set(_REGISTRY) - set(get_args(AgentProvider))
    if unwired:
        raise ImproperlyConfigured(
            f"registered provider(s) missing from the wire AgentProvider Literal: {', '.join(sorted(unwired))} "
            "(add the name to openbower_schema.agents.AgentProvider and run `make schema`)"
        )


def provider_names() -> list[str]:
    """Every registered spec name, in registration (= catalog) order."""
    return list(_REGISTRY)


def model_timeout_exceptions() -> tuple[type[Exception], ...]:
    """Every type a model call can raise for running out of time, one
    declared per provider beside its implementation. A CALLED accessor,
    never a module tuple: the roster fills at ready(), after every
    consumer's import. Only the SDK types: no provider calls the
    transport during a completion, so httpx's own timeout has no
    writer here, and carrying it would be a member whose comment names
    a path that does not exist."""
    return tuple(provider.timeout_exception for provider in _REGISTRY.values())


def catalog_entries() -> tuple[list[tuple[str, str, str]], bool]:
    """Every runnable (provider, source, model) on this deploy,
    providers in registration order, sources in env order, models as
    each source ranks them; the flag says the cap CUT the list (the
    wire carries it: a silent cap would poison the vanished-model
    diagnosis for addresses that still run, since model_for validates
    against the full roster). Probes fan out CONCURRENTLY (a cold
    catalog costs the slowest source, never the sum of timeouts) and
    reassemble in the deterministic registration/source order."""
    pairs = [(name, impl, source_name) for name, impl in _REGISTRY.items() for source_name in impl.sources()]
    if not pairs:
        return [], False
    with ThreadPoolExecutor(max_workers=min(CATALOG_PROBE_CONCURRENCY, len(pairs))) as pool:
        rosters = list(pool.map(lambda pair: pair[1].models(pair[2]), pairs))
    entries = [
        (name, source_name, model)
        for (name, _impl, source_name), roster in zip(pairs, rosters, strict=True)
        for model in roster
    ]
    truncated = len(entries) > CATALOG_MAX_MODELS
    if truncated:
        logger.warning("catalog truncated to %d of %d models", CATALOG_MAX_MODELS, len(entries))
        entries = entries[:CATALOG_MAX_MODELS]
    return entries, truncated


def source_config(provider: str, source: str) -> SourceConfig:
    """One address's configured source. Raises ModelUnavailable on an
    unknown address, same tier as model_for, so a stale saved address
    fails identically whatever the caller came to read."""
    impl = _REGISTRY.get(provider)
    if impl is None:
        raise ModelUnavailable(f"unknown provider spec {provider!r}")
    config = impl.source_config(source)
    if config is None:
        raise ModelUnavailable(f"no source named {source!r} on this deploy")
    return config


def model_for(provider: str, source: str, model: str) -> Model:
    # The registry lookup alone decides "unknown provider": a
    # ValueError out of SDK model construction must not be
    # misattributed to it.
    impl = _REGISTRY.get(provider)
    if impl is None:
        raise ModelUnavailable(f"unknown provider in address {provider}/{source}/{model}")
    runnable = impl.pydantic_model(source, model)
    if runnable is None:
        raise ModelUnavailable(f"source unknown or closed for address {provider}/{source}/{model}")
    roster = impl.models(source)
    if roster and model not in roster:
        # The third component of the address, checked against the
        # roster the impl already probed (cached, so this is free). An
        # EMPTY roster is taken on faith rather than blocking runs on
        # /models uptime: a wrong name then fails at completion time as
        # a config-tier refusal.
        raise ModelUnavailable(f"model not on the source's roster for address {provider}/{source}/{model}")
    return runnable

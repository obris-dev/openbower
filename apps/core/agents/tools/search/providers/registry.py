"""The provider registry: name -> ProviderSpec, with ONE guarded
write path, the same discipline as the tool registry: each provider
MODULE registers itself at its own bottom (`register(SPEC)`), the
roster lives in AgentsConfig.ready(), validation is all-or-nothing
BEFORE the registry mutates, a name collision between two different
specs fails loud, and re-registering the same spec is idempotent.
Unlike the tool registry, no order is load-bearing here: providers
carry no blame order, because the settings switch names exactly ONE
to serve."""

from __future__ import annotations

from ....constants import SearchStatus
from .base import PROVIDER_NAME_MAX_LENGTH, ProviderSpec

_REGISTRY: dict[str, ProviderSpec] = {}


def register(provider: ProviderSpec) -> None:
    """Register one provider. Raises ValueError on an invalid spec or
    a name collision; re-registering the same spec object is a no-op."""
    _validate(provider)
    existing = _REGISTRY.get(provider.name)
    if existing is not None:
        if existing is provider:
            return
        raise ValueError(f"search provider {provider.name!r} is already registered by another spec")
    _REGISTRY[provider.name] = provider


def _validate(provider: ProviderSpec) -> None:
    # provider.name is the DERIVED name (run's own, never declared),
    # so the shape check guards against functions with no usable name:
    # a lambda's "<lambda>" must never become a registered provider.
    if not provider.name.isidentifier() or len(provider.name) > PROVIDER_NAME_MAX_LENGTH:
        raise ValueError(
            f"provider name {provider.name!r} must be a valid identifier of at most {PROVIDER_NAME_MAX_LENGTH} chars"
        )
    if not callable(provider.run) or not callable(provider.usable):
        raise ValueError(f"provider {provider.name!r} must declare callable run and usable")


def get(name: str) -> ProviderSpec:
    """Raises KeyError when the name has no registered provider."""
    return _REGISTRY[name]


def all_providers() -> list[ProviderSpec]:
    """Every registered provider, in registration order."""
    return list(_REGISTRY.values())


def provider_status(provider: str) -> SearchStatus:
    """A provider's status BEFORE any call: NOT_CONFIGURED for an
    unregistered name or one whose deploy lacks its credentials, OPEN
    otherwise. Gates the tools (the runtime never offers a tool whose
    provider is not open) and the catalog. An unregistered name FOLDS
    into NOT_CONFIGURED rather than raising: the switch is deploy
    config, and a bad value must read as "set this up", never take
    the app down."""
    entry = _REGISTRY.get(provider)
    if entry is None or not entry.usable():
        return SearchStatus.NOT_CONFIGURED
    return SearchStatus.OPEN

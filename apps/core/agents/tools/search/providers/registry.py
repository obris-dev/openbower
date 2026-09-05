"""The provider registry: name -> ProviderSpec, with ONE guarded
write path, the same discipline as the tool registry: each provider
MODULE registers itself at its own bottom (`register(SPEC)`), the
roster lives in AgentsConfig.ready(), validation is all-or-nothing
BEFORE the registry mutates, a name collision between two different
specs fails loud, and re-registering the same spec is idempotent.
Unlike the tool registry, no order is load-bearing here: providers
carry no blame order, because each tool's wiring names exactly ONE
to serve."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

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
    if not callable(provider.run):
        raise ValueError(f"provider {provider.name!r} must declare a callable run")
    if provider.config_schema is not None:
        # The schema must be a dataclass of str fields: field names
        # are the operator's table keys, and every value is a secret
        # or an id, never a number the parser would refuse anyway.
        if not is_dataclass(provider.config_schema) or not fields(provider.config_schema):
            raise ValueError(f"provider {provider.name!r} config_schema must be a dataclass with at least one field")
        if not all(field.type in ("str", str) for field in fields(provider.config_schema)):
            raise ValueError(f"provider {provider.name!r} config_schema fields must all be str")
    if not isinstance(provider.metered, bool):
        raise ValueError(f"provider {provider.name!r} metered must be a bool")
    # bool subclasses int; "timeout_seconds = True" must not slip
    # through as 1.
    if isinstance(provider.timeout_seconds, bool) or not isinstance(provider.timeout_seconds, int):
        raise ValueError(f"provider {provider.name!r} timeout_seconds must be an integer")
    if provider.timeout_seconds < 1:
        raise ValueError(f"provider {provider.name!r} timeout_seconds must be at least 1")
    if not provider.display or not isinstance(provider.display, str):
        raise ValueError(f"provider {provider.name!r} must declare a non-empty display name")


def get(name: str) -> ProviderSpec:
    """Raises KeyError when the name has no registered provider."""
    return _REGISTRY[name]


def all_providers() -> list[ProviderSpec]:
    """Every registered provider, in registration order."""
    return list(_REGISTRY.values())


def vendor_table(provider: str) -> dict[str, str]:
    """The vendor's credential table as the operator wrote it ({}
    when absent), read at call time because the config resolves at
    settings import and tests override it."""
    from django.conf import settings

    return settings.TOOL_VENDOR_KEYS.get(provider, {})


def provider_status(provider: str) -> SearchStatus:
    """A provider's status BEFORE any call: NOT_CONFIGURED for an
    unregistered name or one whose vendor table lacks a declared
    credential key, OPEN otherwise. Gates the tools (the runtime
    never offers a tool whose provider is not open) and the catalog.
    An unregistered name FOLDS into NOT_CONFIGURED rather than
    raising: the wiring is deploy config, and a bad value must read
    as "set this up", never take the app down. An empty declared key
    reads as absent: a blank credential can never open a vendor."""
    entry = _REGISTRY.get(provider)
    if entry is None:
        return SearchStatus.NOT_CONFIGURED
    table = vendor_table(provider)
    if not all(table.get(key) for key in entry.config_keys):
        return SearchStatus.NOT_CONFIGURED
    return SearchStatus.OPEN

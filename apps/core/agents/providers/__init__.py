"""The inference providers, as a registry package: `base` is the
CONTRACT a provider implements (the InferenceProvider ABC plus the
custody and caching every spec shares), `registry` the guarded index,
and each spec a module of its own. DELIBERATELY inert beyond the
registry surface: the roster lives in AgentsConfig.ready(), Django's
registration point, so importing this package never drags a provider
SDK in or re-enters a half-initialized module."""

from .registry import (
    ModelUnavailable,
    catalog_entries,
    model_for,
    model_timeout_exceptions,
    provider_names,
    register,
    source_config,
)

__all__ = [
    "ModelUnavailable",
    "catalog_entries",
    "model_for",
    "model_timeout_exceptions",
    "provider_names",
    "register",
    "source_config",
]

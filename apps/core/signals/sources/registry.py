"""The source-kind registry: KIND -> SourceConfig class, with one guarded
write path, the node-kind registry's discipline (sheets.nodes.registry):
each kind module registers itself at its own bottom, the roster is
SignalsConfig.ready()'s walk, validation runs before the registry
mutates, a second class under a taken kind fails loud, and
re-registering the same class is a no-op. The registry holds the roster,
the boot gate, and the one dynamic entry (`parse_source_config`) for a
stored config; everything else about a kind lives on its class (see
base.py)."""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from ..constants import SOURCE_KIND_MAX_LENGTH
from .base import POLLED_SEAM, SEAMS, SourceConfig

_REGISTRY: dict[str, type[SourceConfig]] = {}


class UnknownSourceKind(ValueError):
    """A stored or handed-over config that names no registered kind (or is
    no config at all)."""

    def __init__(self, value: Any) -> None:
        super().__init__(f"no registered source kind for config {value!r}")
        self.value = value


# The kinds the services write by name; the boot gate refuses a roster
# without any of them. Named here rather than read off the kind modules
# because the gate must not import the module it is checking for, or it
# could never fail.
HOSTED = "hosted"
SERVICE_WRITTEN_KINDS: tuple[str, ...] = (HOSTED,)


def register(cls: type[SourceConfig]) -> None:
    """Register one kind. Raises ValueError on an invalid class or a KIND
    collision; re-registering the same class is a no-op."""
    _validate(cls)
    existing = _REGISTRY.get(cls.KIND)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(f"source kind {cls.KIND!r} is already registered by another class")
    _REGISTRY[cls.KIND] = cls


def _validate(cls: type[SourceConfig]) -> None:
    """The whole declaration, checked before any mutation; these surface at
    import time, so each names the registration it refuses."""
    if not (isinstance(cls, type) and issubclass(cls, SourceConfig)):
        raise ValueError(f"a source kind must be a SourceConfig subclass, got {cls!r}")
    kind = getattr(cls, "KIND", None)
    if not isinstance(kind, str) or not kind.isidentifier() or len(kind) > SOURCE_KIND_MAX_LENGTH:
        raise ValueError(
            f"source kind KIND {kind!r} must be a valid identifier of at most {SOURCE_KIND_MAX_LENGTH} chars"
        )
    polled = getattr(cls, "POLLED", None)
    if not isinstance(polled, bool):
        raise ValueError(f"source kind {kind!r} must declare POLLED")
    for seam in SEAMS:
        if _is_default(cls, seam):
            raise ValueError(f"source kind {kind!r} must answer {seam}()")
    answers_poll = not _is_default(cls, POLLED_SEAM)
    if polled and not answers_poll:
        raise ValueError(f"source kind {kind!r} is polled and must answer {POLLED_SEAM}()")
    if answers_poll and not polled:
        raise ValueError(f"source kind {kind!r} answers {POLLED_SEAM}() but is not polled")


def _is_default(cls: type[SourceConfig], seam: str) -> bool:
    return getattr(cls, seam) is getattr(SourceConfig, seam)


def get(kind: str) -> type[SourceConfig]:
    """Raises KeyError when the name has no registered kind."""
    return _REGISTRY[kind]


def all_kinds() -> list[type[SourceConfig]]:
    """Every registered kind, in registration order."""
    return list(_REGISTRY.values())


def parse_source_config(value: Any) -> SourceConfig:
    """A typed config from whatever a caller or the database hands over:
    a JSON string, a dict, or an already-typed value (idempotent). Raises
    UnknownSourceKind for anything that names no registered kind and
    pydantic.ValidationError for a malformed config of a known one."""
    if isinstance(value, SourceConfig):
        return value
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise UnknownSourceKind(value)
    cls = _REGISTRY.get(value.get("kind"))
    if cls is None:
        raise UnknownSourceKind(value)
    return cls.model_validate(value)


def dump_source_config(value: Any) -> dict[str, Any]:
    """A config's dump at rest, from anything parse_source_config takes."""
    config = parse_source_config(value)
    return config.model_dump(mode="json")


def validate_source_kinds() -> None:
    """The boot gate, run after the roster walk: every kind the services
    write is registered. The wire Literal is pinned to the roster by a
    parity test (signals.tests.test_signal_sources)."""
    missing = [kind for kind in SERVICE_WRITTEN_KINDS if kind not in _REGISTRY]
    if missing:
        raise ImproperlyConfigured(
            f"source kinds {missing!r} are not registered (roster: {sorted(_REGISTRY) or 'empty'}); "
            "signals.sources must contain their modules"
        )

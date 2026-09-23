"""The node-kind registry: KIND -> NodeConfig class, with ONE guarded
write path, the search-provider registry's discipline: each kind MODULE
registers itself at its own bottom, the roster lives in
ListsConfig.ready(), validation is all-or-nothing BEFORE the registry
mutates, a name collision between two classes fails loud, and
re-registering the same class is idempotent. The registry holds only
the roster, the boot gate, and the one dynamic entry (`parse_config`)
for a stored row whose kind arrives as a column; everything else about
a kind lives on its class (see base.py). No order is load-bearing
here: kinds carry no rank, so `all_kinds()` is a set in registration
order."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from ..constants import NODE_KIND_MAX_LENGTH
from .base import NodeConfig

_REGISTRY: dict[str, type[NodeConfig]] = {}

# The kinds the services write by name; the boot gate refuses a roster
# without any of them. Named here rather than read off the kind modules
# because the gate must not import the module it is checking for, or it
# could never fail.
COLUMN_AGENT = "column_agent"
ENTRY = "entry"
WAIT_UNTIL = "wait_until"
WEBHOOK = "webhook"
SERVICE_WRITTEN_KINDS: tuple[str, ...] = (COLUMN_AGENT, ENTRY, WAIT_UNTIL, WEBHOOK)
# The kinds that HEAD a path, one of them on every path: entry (fed by
# nothing) and wait_until (fed by the paths it names). The writer
# refuses a path that starts with anything else and never displaces
# one; a reaction skips the head and offers the node behind it. Named
# here, beside the names, so the writer checks for a marker without
# importing a kind module. A marker that ENDS a path (a fan-out branch,
# naming the paths it feeds) belongs to its own set, not this one: the
# two are asked opposite questions, so a caller that means one must
# never be handed the other.
HEAD_OF_PATH_MARKERS: frozenset[str] = frozenset({ENTRY, WAIT_UNTIL})


def register(cls: type[NodeConfig]) -> None:
    """Register one kind. Raises ValueError on an invalid class or a KIND
    collision; re-registering the same class is a no-op."""
    _validate(cls)
    existing = _REGISTRY.get(cls.KIND)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(f"node kind {cls.KIND!r} is already registered by another class")
    _REGISTRY[cls.KIND] = cls


def _validate(cls: type[NodeConfig]) -> None:
    """The whole declaration, checked before any mutation. Each check
    names the registration it refuses, because these errors surface at
    import time where a bare assertion would read as a framework bug."""
    if not (isinstance(cls, type) and issubclass(cls, NodeConfig)):
        raise ValueError(f"a node kind must be a NodeConfig subclass, got {cls!r}")
    kind = getattr(cls, "KIND", None)
    if not isinstance(kind, str) or not kind.isidentifier() or len(kind) > NODE_KIND_MAX_LENGTH:
        raise ValueError(f"node kind KIND {kind!r} must be a valid identifier of at most {NODE_KIND_MAX_LENGTH} chars")
    display = getattr(cls, "DISPLAY", None)
    if not display or not isinstance(display, str):
        raise ValueError(f"node kind {kind!r} must declare a non-empty DISPLAY")
    if cls._identity is NodeConfig._identity:
        raise ValueError(f"node kind {kind!r} must declare its identity projection")


def get(kind: str) -> type[NodeConfig]:
    """Raises KeyError when the name has no registered kind."""
    return _REGISTRY[kind]


def all_kinds() -> list[type[NodeConfig]]:
    """Every registered kind, in registration order."""
    return list(_REGISTRY.values())


def parse_config(kind: str, blob: dict) -> NodeConfig:
    """The dynamic entry: a blob whose kind arrived as a string (a stored
    row's column) -> that kind's typed config. Raises KeyError for an
    unknown kind and pydantic.ValidationError for a malformed blob. A
    caller that knows its kind calls the class's model_validate instead."""
    return get(kind).model_validate(blob)


def validate_node_kinds() -> None:
    """The boot gate, run after the roster walk: every kind the services
    write must be registered. When node kinds reach the wire, the parity
    pin holding the wire Literal to this roster attaches here."""
    missing = [kind for kind in SERVICE_WRITTEN_KINDS if kind not in _REGISTRY]
    if missing:
        raise ImproperlyConfigured(
            f"node kinds {missing!r} are not registered (roster: {sorted(_REGISTRY) or 'empty'}); "
            "lists.nodes must contain their modules"
        )

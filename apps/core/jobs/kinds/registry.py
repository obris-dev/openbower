"""The job-kind registry: KIND -> JobKind class, with ONE guarded write
path (the node-kind registry's discipline): each kind module registers
itself at its own bottom, the roster is JobsConfig.ready()'s walk of
every app's `jobs` package, validation is all-or-nothing BEFORE the
registry mutates, a name collision between two classes fails loud, and
re-registering the same class is idempotent. No order is load-bearing."""

from __future__ import annotations

from pydantic import BaseModel

from ..constants import JOB_KIND_MAX_LENGTH
from .base import JobKind

_REGISTRY: dict[str, type[JobKind]] = {}


def register(cls: type[JobKind]) -> None:
    """Register one kind. Raises ValueError on an invalid class or a KIND
    collision; re-registering the same class is a no-op."""
    _validate(cls)
    existing = _REGISTRY.get(cls.KIND)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(f"job kind {cls.KIND!r} is already registered by another class")
    _REGISTRY[cls.KIND] = cls


def _validate(cls: type[JobKind]) -> None:
    if not (isinstance(cls, type) and issubclass(cls, JobKind)):
        raise ValueError(f"a job kind must be a JobKind subclass, got {cls!r}")
    kind = getattr(cls, "KIND", None)
    if not isinstance(kind, str) or not kind.isidentifier() or len(kind) > JOB_KIND_MAX_LENGTH:
        raise ValueError(f"job kind KIND {kind!r} must be a valid identifier of at most {JOB_KIND_MAX_LENGTH} chars")
    progress = getattr(cls, "Progress", None)
    if not (isinstance(progress, type) and issubclass(progress, BaseModel)):
        raise ValueError(f"job kind {kind!r} must declare a Progress model for its cursor")
    if cls.run is JobKind.run:
        raise ValueError(f"job kind {kind!r} must declare run()")


def get(kind: str) -> type[JobKind]:
    """Raises KeyError when the name has no registered kind."""
    return _REGISTRY[kind]


def all_kinds() -> list[type[JobKind]]:
    return list(_REGISTRY.values())


def parse_payload(kind: str, blob: dict) -> JobKind:
    """The dynamic entry: a stored job's kind arrives as a column, so
    its payload is parsed by name. Raises KeyError for an unknown kind
    and pydantic.ValidationError for a malformed payload."""
    return get(kind).model_validate(blob)

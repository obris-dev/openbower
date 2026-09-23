"""The column-kind registry: the wire's column `kind` -> ColumnKind,
with the node registry's discipline (each kind module registers at
its own bottom, validation before any mutation, a collision loud,
re-registration idempotent) and a boot gate holding the roster to the
contract's column union."""

from __future__ import annotations

from typing import Annotated, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured

from openbower_schema.lists import ListColumn

from .base import ColumnKind

_REGISTRY: dict[str, ColumnKind] = {}


def register(cls: type[ColumnKind]) -> None:
    if not (isinstance(cls, type) and issubclass(cls, ColumnKind)):
        raise ValueError(f"a column kind must be a ColumnKind subclass, got {cls!r}")
    kind = getattr(cls, "KIND", None)
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"column kind {cls.__name__} must declare the KIND it handles")
    existing = _REGISTRY.get(kind)
    if existing is not None:
        if type(existing) is cls:
            return
        raise ValueError(f"column kind {kind!r} is already registered by another class")
    _REGISTRY[kind] = cls()


def column_kind_for(column: ListColumn) -> ColumnKind:
    """The kind for one column, by the wire's discriminator. Raises
    KeyError for a kind the roster lacks (the boot gate makes that a
    deploy gap, never a per-request surprise)."""
    return _REGISTRY[column.kind]


def registered_kinds() -> list[str]:
    return list(_REGISTRY)


def wire_column_kinds() -> list[str]:
    """Every `kind` literal in the contract's column union (a PEP 695
    alias over an Annotated union: unwrapped in that order)."""
    union = getattr(ListColumn, "__value__", ListColumn)
    if get_origin(union) is Annotated:
        union = get_args(union)[0]
    kinds = [get_args(cls.model_fields["kind"].annotation)[0] for cls in get_args(union)]
    if not kinds:
        raise ImproperlyConfigured("the contract's column union names no kinds; the gate cannot hold")
    return kinds


def validate_column_kinds() -> None:
    """The boot gate: every column the wire can carry has a kind that
    knows how its cells change."""
    missing = [kind for kind in wire_column_kinds() if kind not in _REGISTRY]
    if missing:
        raise ImproperlyConfigured(f"column kinds without a registered ColumnKind: {missing}")

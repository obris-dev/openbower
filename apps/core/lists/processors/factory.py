"""The processor roster and the one way to get a processor: by the
node's kind. The registry discipline of lists/nodes/registry.py: each
processor module registers at its own bottom, validation before any
mutation, a collision loud, re-registration idempotent."""

from __future__ import annotations

from ..models import Node
from .base import NodeProcessor

_REGISTRY: dict[str, type[NodeProcessor]] = {}


class UnknownProcessor(Exception):
    """No processor is registered for the node's kind: a walker cannot
    materialize runs for it. Loud, because a kind that reaches a walker
    without a processor is a deploy gap, not a per-row hazard."""


def register(cls: type[NodeProcessor]) -> None:
    if not (isinstance(cls, type) and issubclass(cls, NodeProcessor)):
        raise ValueError(f"a processor must be a NodeProcessor subclass, got {cls!r}")
    kind = getattr(cls, "KIND", None)
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"processor {cls.__name__} must declare the KIND it processes")
    existing = _REGISTRY.get(kind)
    if existing is not None:
        if existing is cls:
            return
        raise ValueError(f"a processor for kind {kind!r} is already registered by another class")
    _REGISTRY[kind] = cls


def processor_for(*, account_id: str, node: Node) -> NodeProcessor:
    """The processor for one node, by its kind."""
    try:
        cls = _REGISTRY[node.kind]
    except KeyError as e:
        raise UnknownProcessor(node.kind) from e
    return cls(account_id=account_id, node=node)


def registered_kinds() -> list[str]:
    return list(_REGISTRY)

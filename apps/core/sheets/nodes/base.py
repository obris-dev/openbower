"""What a node kind IS, and the pattern for adding one.

A kind is one module in this package exporting one class: a NodeConfig
subclass whose class-level KIND and DISPLAY name it, whose fields are
the per-node config at rest, and whose `_identity` is the projection
the unique key indexes (blank for a kind that is never looked up by
identity: its node is addressed by path and rank, and the key skips a
blank). The class is registered at the module's bottom
(`register(ColumnAgent)`); the roster is ListsConfig.ready()'s walk of
this package. A consumer CONSTRUCTS an instance and works with it:
`config.KIND`, `config.identity()`, `config.model_dump()`; a reader
that knows its kind parses with `ColumnAgent.model_validate(blob)`;
only a stored row whose kind arrives as a column goes through the
registry by name (`registry.parse_config`). The kind rides the same
object as its values, so a config can never be stored under another
kind's name.

To add a kind: new module here, a NodeConfig subclass, `register(...)`
at the bottom. Nothing in the registry or the services changes.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ..constants import NODE_IDENTITY_MAX_LENGTH


class NodeConfig(BaseModel):
    KIND: ClassVar[str]
    DISPLAY: ClassVar[str]

    def identity(self) -> str:
        """The projection of this config that identifies its node within
        a workflow (the get-or-create key), or "" for a kind with none.
        Bounded HERE, once: a projection that is not a str, or one past
        the column bound, is refused rather than let the database
        truncate a key."""
        value = self._identity()
        if not isinstance(value, str):
            raise ValueError(f"node kind {self.KIND!r} identity must be a str, got {type(value).__name__}")
        if len(value) > NODE_IDENTITY_MAX_LENGTH:
            raise ValueError(f"node kind {self.KIND!r} identity exceeds {NODE_IDENTITY_MAX_LENGTH} chars")
        return value

    def _identity(self) -> str:
        """The kind's own projection; every kind declares one."""
        raise NotImplementedError

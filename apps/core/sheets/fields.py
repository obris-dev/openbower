"""The typed column array: in memory `Sheet.columns` is a list of the
contract's SheetColumn values (one kind each: plain, AI, webhook), at
rest it is the JSON array of their dumps. One seam, so no reader
restates the shape by hand, and a column the contract refuses (an
unknown kind, a missing key, a stored column with no kind) fails at
parse time: never inferred, never carried.

Two parse points, both needed: `from_db_value` serves the reads that
skip the descriptor (`values()`, `values_list()`), and the descriptor
serves assignment, so the attribute is never a mixed list whichever
path set it. A typed member passes through the union unchanged
(pydantic does not revalidate instances), which makes the second pass
free and means the field holds the instances it was handed; the
members are frozen, so a shared one is safe.

Presents itself to migrations as a plain JSONField. The column IS a
plain jsonb, and this module imports the contract package, so a
migration naming it would break a fresh migrate on the next contract
rename (a kernel field class in a migration is fine; a contract-aware
one is not). The obligation that buys: makemigrations sees nothing
when a member's stored shape changes, so once released such a change
needs a hand-written data migration that nothing will prompt for.

This is the field route for a contract value in a JSON column. Node
config takes the service route (parsed by its kind's class through
the registry) because its kind is a separate column and the roster is
open; a closed union every reader touches belongs on the field. A
signal's source config takes the field route though its roster is open
too: every read of a source wants it typed, and its kind rides inside
the stored config, so the field parses it alone (the kind column beside
it is written from the config by save()). A cell
record's `detail` is the second typed column, so the parse/dump pair
is one parametrized base here (`TypedJSONField`), each field naming
its adapter. It stays app-local: the kernel carries no pydantic
dependency."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from django.db import models
from django.db.models.fields.json import KeyTransform
from django.db.models.query_utils import DeferredAttribute
from pydantic import TypeAdapter

from openbower_schema.sheets import SheetColumn

from .cells import CellDetail


class _TypedAttribute(DeferredAttribute):
    def __set__(self, instance: models.Model, value: Any) -> None:
        instance.__dict__[self.field.attname] = self.field.parse(value)


class TypedJSONField(models.JSONField):
    """A contract value in a JSON column: typed in memory, its dump at
    rest, a plain JSONField to migrations. A subclass names its
    adapter; a nullable field keeps None as None."""

    adapter: ClassVar[TypeAdapter[Any]]
    descriptor_class = _TypedAttribute

    def parse(self, value: Any) -> Any:
        """Typed values from whatever a caller or the database hands
        over: a JSON string, dicts, or already-typed values (idempotent)."""
        if value is None and self.null:
            return None
        if isinstance(value, str):
            value = json.loads(value)
        return self.adapter.validate_python(value)

    def dump(self, value: Any) -> Any:
        parsed = self.parse(value)
        if parsed is None:
            return None
        return self.adapter.dump_python(parsed)

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        value = super().from_db_value(value, expression, connection)
        # A key transform selects a fragment of the value, not the value.
        if isinstance(expression, KeyTransform):
            return value
        return self.parse(value)

    def to_python(self, value: Any) -> Any:
        return self.parse(value)

    def get_db_prep_value(self, value: Any, connection: Any, prepared: bool = False) -> Any:
        return super().get_db_prep_value(self.dump(value), connection, prepared)

    def validate(self, value: Any, model_instance: models.Model | None) -> None:
        super().validate(self.dump(value), model_instance)

    def value_to_string(self, obj: models.Model) -> Any:
        # The serializer encodes; a JSONField hands it the value.
        return self.dump(self.value_from_object(obj))

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.JSONField", args, kwargs


class SheetColumnsField(TypedJSONField):
    """The columns array: the contract's SheetColumn values in memory,
    their dumps at rest."""

    adapter = TypeAdapter(list[SheetColumn])


class CellDetailField(TypedJSONField):
    """A cell record's detail: what its writer said about the cell
    beyond its state, null when nothing."""

    adapter = TypeAdapter(CellDetail)

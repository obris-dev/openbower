"""The typed column array: in memory `List.columns` is a list of the
contract's ListColumn values, at rest it is the JSON array of their
dumps. One seam, so no reader restates the shape by hand (`column.get(
"fill") or {}`) and a key the contract does not know fails at parse
time, not wherever a reader next trips over it.

Coerces on ASSIGNMENT as well as on load: a caller that sets dicts
(the create serializer, a CSV import, a test) finds typed values the
moment it reads them back, never a mixed list that depends on whether
the row was reloaded.

Presents itself to migrations as a plain JSONField: the column IS a
plain jsonb, and a migration that imported this module would break a
fresh migrate on the next rename (the rule every migration header
states)."""

from __future__ import annotations

import json
from typing import Any

from django.db import models
from django.db.models.query_utils import DeferredAttribute

from openbower_schema.lists import ListColumn


def parse_columns(value: Any) -> list[ListColumn]:
    """Typed values from whatever a caller or the database hands over:
    a JSON string, dicts, or already-typed values (idempotent)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [column if isinstance(column, ListColumn) else ListColumn.model_validate(column) for column in value]


def dump_columns(value: Any) -> list[dict[str, Any]]:
    return [column.model_dump() for column in parse_columns(value)]


class _ColumnsAttribute(DeferredAttribute):
    def __set__(self, instance: models.Model, value: Any) -> None:
        instance.__dict__[self.field.attname] = parse_columns(value)


class ListColumnsField(models.JSONField):
    descriptor_class = _ColumnsAttribute

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> list[ListColumn]:
        return parse_columns(super().from_db_value(value, expression, connection))

    def to_python(self, value: Any) -> list[ListColumn]:
        return parse_columns(value)

    def get_db_prep_value(self, value: Any, connection: Any, prepared: bool = False) -> Any:
        return super().get_db_prep_value(dump_columns(value), connection, prepared)

    def validate(self, value: Any, model_instance: models.Model | None) -> None:
        super().validate(dump_columns(value), model_instance)

    def value_to_string(self, obj: models.Model) -> str:
        return json.dumps(dump_columns(self.value_from_object(obj)))

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.JSONField", args, kwargs

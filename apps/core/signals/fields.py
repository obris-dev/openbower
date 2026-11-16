"""A source's typed config in a JSON column: in memory the source kind's
config class, at rest its dump. The sheets columns field's seam
(sheets/fields.py), for the signals app's one contract value at rest:
parsed on read and on assignment, and presented to migrations as a plain
JSONField so a migration never imports the config classes."""

from __future__ import annotations

from typing import Any

from django.db import models
from django.db.models.fields.json import KeyTransform
from django.db.models.query_utils import DeferredAttribute

from .sources.registry import dump_source_config, parse_source_config


class _ConfigAttribute(DeferredAttribute):
    def __set__(self, instance: models.Model, value: Any) -> None:
        instance.__dict__[self.field.attname] = parse_source_config(value)


class SourceConfigField(models.JSONField):
    descriptor_class = _ConfigAttribute

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        value = super().from_db_value(value, expression, connection)
        # A key transform selects a fragment of the config, not the config.
        if isinstance(expression, KeyTransform):
            return value
        return parse_source_config(value)

    def to_python(self, value: Any) -> Any:
        return parse_source_config(value)

    def get_db_prep_value(self, value: Any, connection: Any, prepared: bool = False) -> Any:
        return super().get_db_prep_value(dump_source_config(value), connection, prepared)

    def validate(self, value: Any, model_instance: models.Model) -> None:
        super().validate(dump_source_config(value), model_instance)

    def value_to_string(self, obj: models.Model) -> dict[str, Any]:
        return dump_source_config(self.value_from_object(obj))

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.JSONField", args, kwargs

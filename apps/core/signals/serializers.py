"""Request bodies for the /v1/signals and /v1/signal-previews routes. A setup is given whole,
as the signals service takes it: its filter, with its prompt and threshold
when there are any; the service validates what a setup may say."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers
from rest_framework.settings import api_settings

from openbower_schema import CUSTOM_SIGNAL_TYPE
from sheets.constants import COLUMN_LABEL_MAX_LENGTH, LABEL_MAX_LENGTH, MAX_SHEET_COLUMNS, ColumnType

from .services.logs import DeclaredColumn

# What each type's create takes beyond its type, label and autofill: a
# hosted signal's listener setup, or a custom signal's declared columns.
_HOSTED_FIELDS = frozenset({"filter", "prompt", "min_confidence"})
_CUSTOM_FIELDS = frozenset({"columns"})
# The key a released client sends a signal's type under (v0.0.11 and
# before), refused by name rather than read as a body with no type.
_RETIRED_TYPE_KEY = "signal"


class LogColumnRequest(serializers.Serializer):
    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[column_type.value for column_type in ColumnType])


class _SignalTypeRequest(serializers.Serializer):
    """A body that names a signal's type as `type`, refusing the key a
    released client sends it under by name."""

    type = serializers.CharField()

    def to_internal_value(self, data: Any) -> dict:
        # Before the fields: a body naming the old key has no `type`, and
        # "type is required" would hide which key to rename.
        if isinstance(data, Mapping) and _RETIRED_TYPE_KEY in data:
            detail = f"a signal's type is `type`, not `{_RETIRED_TYPE_KEY}`"
            raise serializers.ValidationError({api_settings.NON_FIELD_ERRORS_KEY: [detail]})
        return super().to_internal_value(data)


class LogCreateRequest(_SignalTypeRequest):
    """A hosted signal ({type: a hosted type, filter, ...}) or a custom
    signal ({type: custom, columns}); each refuses the other's fields by
    name."""

    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    filter = serializers.DictField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    columns = LogColumnRequest(many=True, required=False, max_length=MAX_SHEET_COLUMNS)
    autofill = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs: dict) -> dict:
        sent = set(self.initial_data)
        signal_type = attrs["type"]
        custom = signal_type == CUSTOM_SIGNAL_TYPE
        others = _HOSTED_FIELDS if custom else _CUSTOM_FIELDS
        foreign = sorted(others & sent)
        if foreign:
            raise serializers.ValidationError(f"a {signal_type} signal takes no {', '.join(foreign)}")
        if custom and not attrs.get("columns"):
            raise serializers.ValidationError("a custom signal declares at least one column")
        if not custom and "filter" not in attrs:
            raise serializers.ValidationError(f"a {signal_type} signal names its filter")
        if custom:
            declared = attrs["columns"]
            attrs["columns"] = [DeclaredColumn(label=each["label"], type=ColumnType(each["type"])) for each in declared]
        return attrs


class LogPatchRequest(serializers.Serializer):
    label = serializers.CharField(required=False, max_length=LABEL_MAX_LENGTH)
    filter = serializers.DictField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)

    def validate(self, attrs: dict) -> dict:
        # A key the serializer does not declare is dropped silently, so a
        # caller still sending autofill here would get a 200 that changed
        # nothing; it is refused by name instead.
        if "autofill" in self.initial_data:
            raise serializers.ValidationError("a log's autofill is its sheet's: PATCH /v1/sheets/{id}")
        if ("prompt" in attrs or "min_confidence" in attrs) and "filter" not in attrs:
            raise serializers.ValidationError("a setup is given whole: its filter, with its prompt and min_confidence")
        if not attrs:
            raise serializers.ValidationError("give a label, a setup, or both")
        return attrs


class PreviewCreateRequest(_SignalTypeRequest):
    """A setup tried on a hosted type's events."""

    filter = serializers.DictField()
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    event_ids = serializers.ListField(child=serializers.CharField(), allow_empty=False)

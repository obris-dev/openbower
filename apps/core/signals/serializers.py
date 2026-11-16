"""Request bodies for the /v1/signals and /v1/signal-previews routes. A setup is given whole,
as the signals service takes it: its filter, with its prompt and threshold
when there are any; the service validates what a setup may say."""

from __future__ import annotations

from rest_framework import serializers

from sheets.constants import COLUMN_LABEL_MAX_LENGTH, LABEL_MAX_LENGTH, MAX_SHEET_COLUMNS, ColumnType

from .services.logs import DeclaredColumn
from .sources.registry import HOSTED, WEBHOOK

# What each kind's create takes beyond the label and autofill: a hosted
# signal's listener setup, or a custom signal's declared columns.
_HOSTED_FIELDS = frozenset({"signal", "filter", "prompt", "min_confidence"})
_WEBHOOK_FIELDS = frozenset({"columns"})


class LogColumnRequest(serializers.Serializer):
    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[column_type.value for column_type in ColumnType])


class LogCreateRequest(serializers.Serializer):
    """A hosted signal ({signal, filter, ...}) or a custom signal ({source:
    webhook, columns}); each refuses the other's fields by name."""

    source = serializers.ChoiceField(choices=[HOSTED, WEBHOOK], default=HOSTED)
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    signal = serializers.CharField(required=False)
    filter = serializers.DictField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    columns = LogColumnRequest(many=True, required=False, max_length=MAX_SHEET_COLUMNS)
    autofill = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs: dict) -> dict:
        custom = attrs["source"] == WEBHOOK
        others = _HOSTED_FIELDS if custom else _WEBHOOK_FIELDS
        sent = set(self.initial_data)
        foreign = sorted(others & sent)
        if foreign:
            raise serializers.ValidationError(f"a {attrs['source']} signal takes no {', '.join(foreign)}")
        if custom and not attrs.get("columns"):
            raise serializers.ValidationError("a custom signal declares at least one column")
        if not custom and ("signal" not in attrs or "filter" not in attrs):
            raise serializers.ValidationError("a hosted signal names its signal and filter")
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


class PreviewCreateRequest(serializers.Serializer):
    signal = serializers.CharField()
    filter = serializers.DictField()
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    event_ids = serializers.ListField(child=serializers.CharField(), allow_empty=False)

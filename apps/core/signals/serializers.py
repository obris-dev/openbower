"""Request bodies for the logs and signals routes. A setup is given whole,
as the signals service takes it: its filter, with its prompt and threshold
when there are any; the service validates what a setup may say."""

from __future__ import annotations

from rest_framework import serializers

from openbower_schema.sheets import COLUMN_LABEL_MAX_LENGTH
from sheets.constants import LABEL_MAX_LENGTH, MAX_SHEET_COLUMNS, ColumnType

from .constants import LogSource

# What each source's create takes beyond the label and autofill: a hosted
# signal's listener setup, or a custom signal's declared columns.
_HOSTED_FIELDS = frozenset({"signal", "filter", "prompt", "min_confidence"})
_WEBHOOK_FIELDS = frozenset({"columns"})


class LogColumnRequest(serializers.Serializer):
    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[column_type.value for column_type in ColumnType])


class LogCreateRequest(serializers.Serializer):
    """A hosted signal's log ({signal, filter, ...}) or a custom signal's
    ({source: webhook, columns}); each refuses the other's fields by name."""

    source = serializers.ChoiceField(choices=[source.value for source in LogSource], default=LogSource.HOSTED.value)
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    signal = serializers.CharField(required=False)
    filter = serializers.DictField(required=False)
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    columns = LogColumnRequest(many=True, required=False, max_length=MAX_SHEET_COLUMNS)
    autofill = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs: dict) -> dict:
        webhook = attrs["source"] == LogSource.WEBHOOK
        foreign = sorted((_HOSTED_FIELDS if webhook else _WEBHOOK_FIELDS) & set(self.initial_data))
        if foreign:
            raise serializers.ValidationError(f"a {attrs['source']} signal takes no {', '.join(foreign)}")
        if webhook and not attrs.get("columns"):
            raise serializers.ValidationError("a custom signal declares at least one column")
        if not webhook and ("signal" not in attrs or "filter" not in attrs):
            raise serializers.ValidationError("a hosted signal names its signal and filter")
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

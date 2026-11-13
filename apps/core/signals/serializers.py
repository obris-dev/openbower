"""Request bodies for the logs and signals routes. A setup is given whole,
as the signals service takes it: its filter, with its prompt and threshold
when there are any; the service validates what a setup may say."""

from __future__ import annotations

from rest_framework import serializers

from sheets.constants import LABEL_MAX_LENGTH


class LogCreateRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    signal = serializers.CharField()
    filter = serializers.DictField()
    prompt = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    min_confidence = serializers.FloatField(required=False, allow_null=True)
    autofill = serializers.BooleanField(required=False, default=True)


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

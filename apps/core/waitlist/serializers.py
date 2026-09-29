"""Request validation for the public signup post. Shape rules refuse
here as DRF field errors; the response constructs the contract model,
so drift between the two fails loudly on the server."""

from __future__ import annotations

from rest_framework import serializers

from .constants import WAITLIST_SOURCE_MAX_LENGTH


class _EmailField(serializers.EmailField):
    """Trimmed and lowercased on the way in: the address is the
    identity, so two spellings of one mailbox must be one row."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return value.strip().lower()


class WaitlistSignupRequest(serializers.Serializer):
    email = _EmailField()
    source = serializers.CharField(required=False, allow_blank=True, max_length=WAITLIST_SOURCE_MAX_LENGTH, default="")

"""Request validation + wire builders for webhooks. SHAPE rules (a
header's grammar, lengths, uniqueness) refuse here as DRF field errors;
judgements that need the account or the deployment (the cap, the URL
guard, a reserved header name) are the service's refusals on the
{error, detail} envelope. Wire builders CONSTRUCT the contract models:
drift between the contract and the views fails loudly here, never in
the client's zod."""

from __future__ import annotations

import re
from typing import Any

from rest_framework import serializers

from openbower_schema.webhooks import WebhookDeliveriesPage, WebhookDeliveryWire, WebhookDestinationWire

from .constants import (
    LABEL_MAX_LENGTH,
    MAX_WEBHOOK_HEADERS,
    WEBHOOK_HEADER_NAME_GRAMMAR,
    WEBHOOK_HEADER_NAME_MAX_LENGTH,
    WEBHOOK_HEADER_VALUE_GRAMMAR,
    WEBHOOK_HEADER_VALUE_MAX_LENGTH,
    WEBHOOK_URL_MAX_LENGTH,
)
from .models import WebhookDelivery, WebhookDestination


def _full_match(grammar: str, value: str, message: str) -> str:
    # A FULL match, never a search: Python's `$` matches before a final
    # newline, so a searched grammar admits "token\n", which stores and
    # then fails on every send. The client applies the same grammar
    # with a strict end anchor, so the two must agree here.
    if re.fullmatch(grammar, value) is None:
        raise serializers.ValidationError(message)
    return value


class WebhookHeaderDef(serializers.Serializer):
    name = serializers.CharField(max_length=WEBHOOK_HEADER_NAME_MAX_LENGTH)
    # Verbatim: a token's trailing space is the user's to keep.
    value = serializers.CharField(max_length=WEBHOOK_HEADER_VALUE_MAX_LENGTH, trim_whitespace=False)

    def validate_name(self, value: str) -> str:
        return _full_match(WEBHOOK_HEADER_NAME_GRAMMAR, value, "header names use letters, digits, and dashes")

    def validate_value(self, value: str) -> str:
        return _full_match(WEBHOOK_HEADER_VALUE_GRAMMAR, value, "header values use printable characters on one line")


def _headers_dict(value: list[dict[str, str]]) -> dict[str, str]:
    """Names are unique case-insensitively (HTTP folds them, so two
    spellings would be one header with an arbitrary value)."""
    names = [header["name"].lower() for header in value]
    if len(set(names)) != len(names):
        raise serializers.ValidationError("header names must be unique")
    return {header["name"]: header["value"] for header in value}


def _http_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise serializers.ValidationError("url must be http or https")
    return value


class DestinationCreateRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    url = serializers.URLField(max_length=WEBHOOK_URL_MAX_LENGTH)
    headers = WebhookHeaderDef(many=True, required=False, default=list, max_length=MAX_WEBHOOK_HEADERS)

    def validate_url(self, value: str) -> str:
        return _http_url(value)

    def validate_headers(self, value: list[dict[str, str]]) -> dict[str, str]:
        return _headers_dict(value)


class DestinationPatchRequest(serializers.Serializer):
    """Any subset; `headers` present replaces the whole set. At least one
    field must be present (the agents idiom)."""

    label = serializers.CharField(max_length=LABEL_MAX_LENGTH, required=False)
    url = serializers.URLField(max_length=WEBHOOK_URL_MAX_LENGTH, required=False)
    enabled = serializers.BooleanField(required=False)
    headers = WebhookHeaderDef(many=True, required=False, max_length=MAX_WEBHOOK_HEADERS)

    def validate_url(self, value: str) -> str:
        return _http_url(value)

    def validate_headers(self, value: list[dict[str, str]]) -> dict[str, str]:
        return _headers_dict(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("nothing to change")
        return attrs


def delivery_wire(delivery: WebhookDelivery) -> dict[str, Any]:
    model = delivery_model(delivery)
    return model.model_dump()


def delivery_model(delivery: WebhookDelivery) -> WebhookDeliveryWire:
    return WebhookDeliveryWire(
        id=str(delivery.id),
        destination_id=delivery.destination_id,
        type=delivery.type,
        test=delivery.test,
        status=delivery.status,
        http_status=delivery.http_status,
        duration_ms=delivery.duration_ms,
        error=delivery.error,
        response_excerpt=delivery.response_excerpt,
        created_at=delivery.created_at.isoformat(),
    )


def destination_wire(
    destination: WebhookDestination,
    *,
    header_names: list[str],
    newest: WebhookDelivery | None,
    column_count: int | None = None,
) -> dict[str, Any]:
    return WebhookDestinationWire(
        id=str(destination.id),
        label=destination.label[:LABEL_MAX_LENGTH],
        url=destination.url,
        header_names=header_names,
        enabled=destination.enabled,
        last_delivery=delivery_model(newest) if newest is not None else None,
        rotated_at=destination.rotated_at.isoformat() if destination.rotated_at else None,
        column_count=column_count,
        created_at=destination.created_at.isoformat(),
    ).model_dump()


def deliveries_page_wire(rows: list[WebhookDelivery], *, next_cursor: str | None) -> dict[str, Any]:
    return WebhookDeliveriesPage(items=[delivery_model(row) for row in rows], next_cursor=next_cursor).model_dump()

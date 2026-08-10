"""Project-wide DRF exception handler.

Maps DRF's built-in failures onto the API's stable
`{"error": <code>, "detail": <msg>}` shape so every app -> web error path
returns one contract: authentication failures become `not_authenticated`;
request-validation failures (a serializer's 400) become `invalid_request`
carrying EVERY message: `detail` is a complete human-readable summary in
field-declaration order, and `fields` maps each field to its messages so
a client can attach errors to inputs instead of a banner (byte-compatible
with the identity service's handler, so the web parses one shape).
Everything else passes through DRF's default handling.
"""

from __future__ import annotations

from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from .constants import AuthErrorCode

_NON_FIELD = "non_field_errors"


def _leaf_messages(value) -> list[str]:
    """Flatten DRF's nested detail (lists of lists, dicts of lists) to the
    human messages, in order."""
    if isinstance(value, dict):
        return [message for child in value.values() for message in _leaf_messages(child)]
    if isinstance(value, list):
        return [message for child in value for message in _leaf_messages(child)]
    return [str(value)]


def _validation_payload(detail) -> tuple[str, dict[str, list[str]]]:
    """(summary, fields): the summary carries every message (field-prefixed,
    declaration order); fields keeps the association for per-input display."""
    if isinstance(detail, dict):
        fields = {str(field): _leaf_messages(messages) for field, messages in detail.items()}
    else:
        fields = {_NON_FIELD: _leaf_messages(detail)}
    parts = []
    for field, messages in fields.items():
        joined = " ".join(messages)
        parts.append(joined if field == _NON_FIELD else f"{field}: {joined}")
    return ("; ".join(parts) or "invalid request", fields)


def auth_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return response
    if isinstance(exc, NotAuthenticated | AuthenticationFailed):
        response.data = {"error": str(AuthErrorCode.NOT_AUTHENTICATED), "detail": str(exc.detail)}
    elif isinstance(exc, ValidationError):
        detail, fields = _validation_payload(exc.detail)
        response.data = {
            "error": str(AuthErrorCode.INVALID_REQUEST),
            "detail": detail,
            "fields": fields,
        }
    return response

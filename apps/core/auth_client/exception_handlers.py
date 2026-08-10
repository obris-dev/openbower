"""Project-wide DRF exception handler.

Maps DRF's built-in failures onto the API's stable
`{"error": <code>, "detail": <msg>}` shape so every app -> web error path
returns one contract (the web reads `body.detail`): authentication failures
become `not_authenticated`, request-validation failures (a serializer's 400)
become `invalid_request` with the first field error flattened into `detail`.
Everything else passes through DRF's default handling.
"""

from __future__ import annotations

from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from .constants import AuthErrorCode


def _first_error_detail(detail) -> str:
    """Flatten DRF's nested ValidationError detail (dict of field -> list,
    or a bare list) to a single human message."""
    if isinstance(detail, dict):
        for value in detail.values():
            return _first_error_detail(value)
        return "invalid request"
    if isinstance(detail, list):
        return _first_error_detail(detail[0]) if detail else "invalid request"
    return str(detail)


def auth_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return response
    if isinstance(exc, NotAuthenticated | AuthenticationFailed):
        response.data = {"error": str(AuthErrorCode.NOT_AUTHENTICATED), "detail": str(exc.detail)}
    elif isinstance(exc, ValidationError):
        response.data = {"error": str(AuthErrorCode.INVALID_REQUEST), "detail": _first_error_detail(exc.detail)}
    return response

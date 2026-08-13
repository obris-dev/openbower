"""The data-service client, shaped like auth_client.services.oauth:
auth (the session credential as an httpx.Auth flow, owning token
attachment and the 401-refresh-retry), transport (the wire calls + error
normalization + contract validation), client (the session binding),
errors (the failure taxonomy callers dispatch on)."""

from .client import IndexClientService, SessionIndexClient
from .errors import (
    IndexAccessDenied,
    IndexClientError,
    IndexClientRequestError,
    IndexUpstreamError,
    IndexUpstreamUnavailable,
)

__all__ = [
    "IndexAccessDenied",
    "IndexClientError",
    "IndexClientRequestError",
    "IndexClientService",
    "IndexUpstreamError",
    "IndexUpstreamUnavailable",
    "SessionIndexClient",
]

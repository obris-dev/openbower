"""The data-service client, shaped like auth_client.services.oauth:
transport (the wire calls + error normalization + contract validation,
with the session credential attached by the shared
auth_client.services.sessions.SessionTokenAuth flow), client (the session
binding), errors (the failure taxonomy callers dispatch on)."""

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

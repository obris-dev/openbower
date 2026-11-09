"""The signals-service client, shaped like discover's index_client:
transport (the wire calls + error normalization + contract validation,
with the session credential attached by the shared
auth_client.services.sessions.SessionTokenAuth flow), client (the session
binding), errors (the failure taxonomy callers dispatch on)."""

from .client import SessionSignalsClient, SignalsClientService
from .errors import (
    SignalsAccessDenied,
    SignalsClientError,
    SignalsClientRequestError,
    SignalsLimitReached,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
)

__all__ = [
    "SessionSignalsClient",
    "SignalsAccessDenied",
    "SignalsClientError",
    "SignalsClientRequestError",
    "SignalsClientService",
    "SignalsLimitReached",
    "SignalsPreviewRunning",
    "SignalsUpstreamError",
    "SignalsUpstreamUnavailable",
]

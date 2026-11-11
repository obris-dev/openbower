"""The signals-service client, in three parts:
transport (the wire calls + error normalization + contract validation,
with the credential the caller binds), client (a session's, or an API
key's), errors (the failure taxonomy callers dispatch on)."""

from .client import KeySignalsClient, SessionSignalsClient, SignalsClientService
from .errors import (
    SignalsAccessDenied,
    SignalsClientError,
    SignalsClientRequestError,
    SignalsKeyRejected,
    SignalsLimitReached,
    SignalsPreviewRunning,
    SignalsUpstreamError,
    SignalsUpstreamUnavailable,
)

__all__ = [
    "KeySignalsClient",
    "SessionSignalsClient",
    "SignalsAccessDenied",
    "SignalsClientError",
    "SignalsClientRequestError",
    "SignalsClientService",
    "SignalsKeyRejected",
    "SignalsLimitReached",
    "SignalsPreviewRunning",
    "SignalsUpstreamError",
    "SignalsUpstreamUnavailable",
]

"""The OAuth client package: `client` is the flow, `transport` the wire,
`schema` the parsed shapes, `pkce` the proof material, `errors` the named
failures. This surface is the public one; reach inside only in tests."""

from .client import OAuthClientService
from .errors import AuthUpstreamError, AuthUpstreamUnavailable, OAuthClientError, StateMismatch
from .schema import TokenResponse

__all__ = [
    "AuthUpstreamError",
    "AuthUpstreamUnavailable",
    "OAuthClientError",
    "OAuthClientService",
    "StateMismatch",
    "TokenResponse",
]

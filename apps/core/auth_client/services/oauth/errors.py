"""Named failures of the login handshake. The transient/terminal split is
the load-bearing distinction: the refresh path must tell "the token is
dead" (revoke the session) apart from "the IdP blinked" (keep it; a brief
outage must not log everyone out)."""


class OAuthClientError(Exception):
    """Base for login-handshake failures."""


class StateMismatch(OAuthClientError):
    """The callback's state is unknown or expired (possible CSRF, an
    expired login attempt, or a replayed callback)."""


class AuthUpstreamError(OAuthClientError):
    """The IdP DEFINITIVELY rejected a server-side call: a 4xx the retry
    won't fix (e.g. 400 invalid_grant on a dead refresh token). Callers
    treat this as terminal, the session is over."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuthUpstreamUnavailable(AuthUpstreamError):
    """The IdP was unreachable or failed TRANSIENTLY (network error,
    timeout, 5xx). Subclasses AuthUpstreamError so callers that don't care
    about the distinction (the login callback) still catch both."""

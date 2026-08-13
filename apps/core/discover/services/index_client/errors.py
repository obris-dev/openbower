"""The data-service client's failure taxonomy.

The split mirrors auth_client.services.oauth.errors: callers choose
behavior by TYPE. Catch order matters where both are handled:
IndexUpstreamUnavailable subclasses IndexUpstreamError, so the transient
case must be caught first.
"""

from __future__ import annotations

from typing import Any


class IndexClientError(Exception):
    """Base for data-service client failures."""


class IndexUpstreamError(IndexClientError):
    """The data service DEFINITIVELY failed the call (unexpected status or
    a malformed body). Terminal for this request."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class IndexUpstreamUnavailable(IndexUpstreamError):
    """Unreachable or failed TRANSIENTLY (network error, timeout, 5xx,
    408/429, or the data service reporting its own IdP outage). Retryable."""


class IndexAccessDenied(IndexClientError):
    """The data service rejected the token on AUTHORIZATION grounds (403):
    it is valid but lacks the required scope. A refresh carries the same
    scopes, so this cannot be retried away; the remedy is a fresh login. A
    token rejected as inactive/expired (401) is the retryable
    `DownstreamTokenRejected` instead, handled by the session's
    refresh-and-retry."""


class IndexClientRequestError(IndexClientError):
    """The data service judged the REQUEST wrong (400 invalid query, 404
    unknown run). Carries the upstream `{error, detail}` body and status so
    the caller can pass both through untouched."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body

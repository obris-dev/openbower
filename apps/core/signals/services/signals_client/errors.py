"""The signals client's failure taxonomy.

Callers choose behavior by TYPE. Catch order matters where both are handled:
SignalsUpstreamUnavailable subclasses SignalsUpstreamError, and the two
409s subclass SignalsClientRequestError, so the narrower case is caught
first.
"""

from __future__ import annotations

from typing import Any

from openbower_schema import PreviewRunningWire


class SignalsClientError(Exception):
    """Base for signals client failures."""


class SignalsUpstreamError(SignalsClientError):
    """The signals service DEFINITIVELY failed the call (unexpected status
    or a malformed body). Terminal for this request."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SignalsUpstreamUnavailable(SignalsUpstreamError):
    """Unreachable or failed TRANSIENTLY (network error, timeout, 5xx,
    408/429, or the service reporting its own IdP outage). Retryable."""


class SignalsAccessDenied(SignalsClientError):
    """The service rejected the token on AUTHORIZATION grounds (403): valid,
    but without the scope the call needs (a session from before signals
    carries none of its scopes). A refresh carries the same scopes, so the
    remedy is a fresh login. A token rejected as inactive or expired (401)
    is the retryable `DownstreamTokenRejected` instead, handled by the
    session's refresh-and-retry."""


class SignalsKeyRejected(SignalsClientError):
    """The service refused an API key (401 or 403): revoked, expired, or
    minted without signals:read. A key has no refresh, so nothing here
    retries it; a person reconnects (mints a fresh one through their
    session). `key_id` names the key refused, so the caller marks THAT key
    and never its successor (a reconnect can land while a call is out)."""

    def __init__(self, *, key_id: str, status: int) -> None:
        super().__init__(f"signals service refused key {key_id} ({status})")
        self.key_id = key_id
        self.status = status


class SignalsClientRequestError(SignalsClientError):
    """The service judged the REQUEST wrong (400 a setup it refuses, 404 no
    such listener or preview, 409 a conflict). Carries the upstream
    `{error, detail}` body and status so the caller can pass both through
    untouched."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body


class SignalsNotFound(SignalsClientRequestError):
    """404: the service has no such listener or preview for this account
    (another account's reads the same)."""


class SignalsLimitReached(SignalsClientRequestError):
    """409: the account already has as many listeners as it may (the
    catalog's `limits.max_listeners`)."""


class SignalsPreviewRunning(SignalsClientRequestError):
    """409: the account has a preview open. `running` names it: wait for it
    to finish, or cancel it, before creating another."""

    def __init__(self, status: int, body: dict[str, Any], *, running: PreviewRunningWire) -> None:
        super().__init__(status, body)
        self.running = running

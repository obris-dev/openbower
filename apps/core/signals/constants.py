"""Wire-level constants for the signals client and its feed's key."""

from __future__ import annotations

from enum import StrEnum

# What the account's sync key may do beyond profile: read listeners and
# their matches, never change them (a person does that as themselves).
SYNC_KEY_SCOPES = ("signals:read",)


# The signals service's 409 codes the client tells apart: each is its own
# error type, so a caller never compares the raw code. Its 400/404 bodies
# pass through with their codes untouched.
class SignalsConflictCode(StrEnum):
    LIMIT_REACHED = "limit_reached"  # the account is at its listener cap
    ALREADY_RUNNING = "already_running"  # the account has a preview open


# The app's own codes on the {error, detail} envelope its logs and signals
# routes answer with; the signals service's own 400/404/409 codes pass
# through untouched beside them.
class SignalsErrorCode(StrEnum):
    SIGNALS_UNAVAILABLE = "signals_unavailable"  # 503: the service is down or answered nonsense
    UNKNOWN_SIGNAL = "unknown_signal"  # 400: a signal this app cannot log
    KEY_LIMIT_REACHED = "key_limit_reached"  # 409: the sync key would exceed the person's key limit


# The copy a key-limit refusal answers with: the person's next step, in the
# app's words (the identity service's detail speaks of keys in general).
KEY_LIMIT_DETAIL = (
    "This account holds as many API keys as it may. Revoke one under Settings, API keys, to connect a log."
)

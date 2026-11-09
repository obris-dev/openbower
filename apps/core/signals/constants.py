"""Wire-level constants for the signals client."""

from __future__ import annotations

from enum import StrEnum


# The signals service's 409 codes the client tells apart: each is its own
# error type, so a caller never compares the raw code. Its 400/404 bodies
# pass through with their codes untouched.
class SignalsConflictCode(StrEnum):
    LIMIT_REACHED = "limit_reached"  # the account is at its listener cap
    ALREADY_RUNNING = "already_running"  # the account has a preview open

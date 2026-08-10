"""Wire-level constants for the app's auth client."""

from __future__ import annotations

from enum import StrEnum

# The opaque app session cookie. Carries a random session token (never an
# IdP token); the server-side AppSession record holds the real credentials.
SESSION_COOKIE_NAME = "bwr_session"


# Stable error codes. NOT_AUTHENTICATED / INVALID_REQUEST ride the JSON
# `{"error", "detail"}` API responses (via the project exception handler);
# the rest are the `?auth_error=` values the login callback hands to the web
# UI, which switches on them. One StrEnum so neither the server nor the web
# hardcodes a loose literal.
class AuthErrorCode(StrEnum):
    NOT_AUTHENTICATED = "not_authenticated"
    INVALID_REQUEST = "invalid_request"
    LOGIN_FAILED = "login_failed"
    MISSING_PARAMS = "missing_params"
    STATE_MISMATCH = "state_mismatch"

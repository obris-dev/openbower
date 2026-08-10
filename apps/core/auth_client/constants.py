"""Wire-level constants for the app's auth client."""

from __future__ import annotations

from enum import StrEnum

# The opaque app session cookie. Carries a random session token (never an
# IdP token); the server-side AppSession record holds the real credentials.
SESSION_COOKIE_NAME = "bwr_session"

# The login round-trip's browser-binding cookie. The state bag store is
# server-global, so state alone proves the flow STARTED, not WHO started
# it: without this cookie a victim lured to an attacker's callback URL
# would be signed into the attacker-initiated session (login CSRF). Set
# at /login, required to match at /callback, cleared either way.
STATE_COOKIE_NAME = "bwr_oauth_state"

# The authorize round-trip should take seconds; 10 minutes absorbs a slow
# first-time login. Shared by the cache bag and the state cookie so the
# two halves of the same handshake expire together.
STATE_TTL_SECONDS = 600


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

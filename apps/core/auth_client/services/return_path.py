"""Where a login lands the browser afterwards: a same-origin PATH.

The kickoff takes it as `next`, the callback appends it to APP_BASE_URL.
Only a plain path is ever accepted: a scheme, a host, a protocol-relative
`//host`, or a backslash (which browsers read as a slash) would turn the
landing into an open redirect, so each of those falls back to "/". The
refusal is silent on purpose: a bad `next` is a wrong link, never a
reason to fail a sign-in.
"""

from __future__ import annotations

from auth_client.constants import RETURN_PATH_MAX_LENGTH

HOME = "/"


def clean_return_path(raw: str | None) -> str:
    """The path to land on after login, or "/" for anything that is not a
    plain same-origin path."""
    if not raw or len(raw) > RETURN_PATH_MAX_LENGTH:
        return HOME
    if not raw.startswith("/") or raw.startswith("//"):
        return HOME
    if "\\" in raw or any(ch.isspace() or ord(ch) < 32 for ch in raw):
        return HOME
    return raw

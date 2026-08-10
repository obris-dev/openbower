"""Hashing helpers for opaque secrets.

Keeps the "store a token by its fingerprint, never its plaintext" pattern in
one place: the app session cookie token uses it now. When the data resource
server lands (a separate service) and needs the same for PAT (`obwd_`)
verification, this is the shape to lift into a shared package.
"""

from __future__ import annotations

import hashlib


def hash_token(raw: str) -> str:
    """SHA-256 hex of an opaque token, for storing / looking it up by
    fingerprint instead of its plaintext (a leaked db dump can't be replayed
    as a live token/cookie)."""
    return hashlib.sha256(raw.encode("ascii")).hexdigest()

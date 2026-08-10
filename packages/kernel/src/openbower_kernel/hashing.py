"""Token fingerprinting.

`hash_token` gives the one-way fingerprint used wherever a raw credential
must be looked up without being stored or logged (e.g. a credential-lookup
cache key). SHA-256 hex, mirroring the suite's other services.
"""

from __future__ import annotations

import hashlib


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

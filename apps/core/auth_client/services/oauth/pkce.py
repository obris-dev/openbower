"""PKCE (RFC 7636) material. The app is a PUBLIC client: no client
secret; possession of the verifier is the proof that the party finishing
the flow is the one that started it."""

from __future__ import annotations

import base64
import hashlib
import secrets


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    return secrets.token_urlsafe(48)


def challenge(verifier: str) -> str:
    """S256: the only method worth supporting (plain exists for clients
    that cannot hash; we can)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

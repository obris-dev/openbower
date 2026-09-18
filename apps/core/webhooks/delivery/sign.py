"""Signing, the Standard Webhooks scheme: a secret `whsec_<base64>`,
and per delivery the id, timestamp (unix seconds), and signature
headers (`v1,` + base64 of HMAC-SHA256 over `"{id}.{timestamp}.{body}"`).
After a rotation the header carries one such value per secret,
space-separated, current first, so a receiver on either secret
verifies. The header names and the secret's shape are the contract's
constants. Pure: no settings, no I/O."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Sequence
from typing import NamedTuple

from pydantic import BaseModel

from ..constants import (
    SIGNING_SECRET_BYTES,
    WEBHOOK_ID_HEADER,
    WEBHOOK_SECRET_PREFIX,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_SIGNATURE_VERSION,
    WEBHOOK_TIMESTAMP_HEADER,
)


def mint_secret() -> str:
    return WEBHOOK_SECRET_PREFIX + base64.b64encode(secrets.token_bytes(SIGNING_SECRET_BYTES)).decode("ascii")


def secret_key(secret: str) -> bytes | None:
    """The HMAC key a stored secret decodes to, or None when the value is
    not a secret this code minted (a rotated encryption key hands back
    ciphertext; the caller refuses to sign rather than sign garbage)."""
    if not secret.startswith(WEBHOOK_SECRET_PREFIX):
        return None
    try:
        return base64.b64decode(secret[len(WEBHOOK_SECRET_PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return None


def encode_body(payload: BaseModel) -> bytes:
    """One serialization: the bytes signed ARE the bytes sent, so the
    sender never re-encodes."""
    return json.dumps(payload.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")


class Signature(NamedTuple):
    """One delivery's signature: the id and timestamp a receiver checks,
    one `v1,` value per signing key (current first), and the headers
    that carry them."""

    delivery_id: str
    timestamp: int
    values: tuple[str, ...]

    @property
    def value(self) -> str:
        """The signature header's value: every key's signature, space
        separated, as the scheme lays them out."""
        return " ".join(self.values)

    def headers(self) -> dict[str, str]:
        return {
            WEBHOOK_ID_HEADER: self.delivery_id,
            WEBHOOK_TIMESTAMP_HEADER: str(self.timestamp),
            WEBHOOK_SIGNATURE_HEADER: self.value,
        }


def sign(*, keys: Sequence[bytes], delivery_id: str, timestamp: int, body: bytes) -> Signature:
    signed = f"{delivery_id}.{timestamp}.".encode() + body
    values = tuple(
        f"{WEBHOOK_SIGNATURE_VERSION},{base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode('ascii')}"
        for key in keys
    )
    return Signature(delivery_id=delivery_id, timestamp=timestamp, values=values)

"""Fernet-encrypted TextField for secrets stored at rest.

The AppSession rows hold the user's IdP access + refresh tokens, which
cannot be hashed (the app must replay them upstream). Encrypting them at
rest means a leaked DB dump does not hand over live refresh tokens. The
key derives from AUTH_TOKEN_ENCRYPTION_KEY (or SECRET_KEY in dev); the
ORM and app code see plaintext, encryption happens only on the DB round
trip.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # Fernet needs a 32-byte urlsafe-base64 key; derive one deterministically
    # from the configured secret so operators supply a normal string.
    secret = settings.AUTH_TOKEN_ENCRYPTION_KEY or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


class EncryptedTextField(models.TextField):
    """TextField transparently Fernet-encrypted at rest.

    NOTE: ciphertext is non-deterministic (random IV), so these columns
    cannot be filtered/looked-up on; that is fine, sessions are found by
    `token_hash`, never by token value.
    """

    def get_prep_value(self, value: str | None) -> str | None:
        if value is None:
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value: str | None, expression, connection) -> str | None:
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Not our ciphertext: a row written before encryption landed, or
            # under a since-rotated key. Return as-is so a legacy plaintext
            # row keeps working until it is rewritten; a wrong-key row will
            # simply fail the downstream refresh and re-login.
            return value

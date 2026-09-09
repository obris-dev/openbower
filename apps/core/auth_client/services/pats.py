"""Personal access tokens: the core-local machine credential behind
`Bearer obw_...`, minted by a logged-in owner and presented by
webhooks, scripts, and producers.

`mint` returns the raw token ONCE and stores only its hash; `resolve`
is the per-request path (hash lookup + validity, with a throttled
last-used stamp whose write must never fail an otherwise valid auth);
`revoke` ends one token. A PAT never authorizes minting another: a
leaked key must not bootstrap credentials that outlive its own
revocation.
"""

from __future__ import annotations

import contextlib
import secrets
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone

from auth_client.models import PersonalAccessToken
from openbower_kernel.hashing import hash_token

# The prefix routes the shared Authorization header (a bearer that
# starts with it is ALWAYS treated as a PAT, never another credential)
# and makes leaked keys secret-scanner matchable, the ghp_/glpat-
# convention.
TOKEN_PREFIX = "obw_"
# 256 bits of entropy, so a plain unsalted SHA-256 at rest is enough:
# the token is unguessable, not a password.
_TOKEN_NBYTES = 32
# last_used_at is telemetry, not audit: one write per token per
# window keeps a busy producer from turning every request into an
# UPDATE.
_LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


class PatService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def mint(self, *, name: str, expires_in_days: int = 0) -> tuple[PersonalAccessToken, str]:
        """Returns `(record, raw_token)`; the raw exists only in this
        return value. 0 days means no expiry timer."""
        raw = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_NBYTES)
        record = PersonalAccessToken.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
            name=name,
            token_hash=hash_token(raw),
            last_four=raw[-4:],
            expires_at=timezone.now() + timedelta(days=expires_in_days) if expires_in_days else None,
        )
        return record, raw

    def list_tokens(self) -> list[PersonalAccessToken]:
        """The owner's live tokens, newest first (revoked ones are
        history, not roster)."""
        return list(PersonalAccessToken.objects.filter(user_id=self.user_id, revoked_at__isnull=True).order_by("-id"))

    def revoke(self, token_id: str) -> bool:
        """Owner-scoped; returns False when the id names nothing the
        owner holds (a foreign token reads as absent, never as
        forbidden)."""
        updated = PersonalAccessToken.objects.filter(id=token_id, user_id=self.user_id, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        return updated > 0


def resolve(raw: str) -> PersonalAccessToken | None:
    """Raw token -> live PAT, or None. The per-request path: pure
    equality lookup on the hash, validity judged locally, and a
    throttled last-used stamp that must never fail the auth (a bare
    queryset update so no signals or updated_at churn ride it, and a
    database hiccup on telemetry is swallowed)."""
    try:
        record = PersonalAccessToken.objects.get(token_hash=hash_token(raw))
    except PersonalAccessToken.DoesNotExist:
        return None
    if not record.is_valid():
        return None
    now = timezone.now()
    if record.last_used_at is None or now - record.last_used_at >= _LAST_USED_WRITE_INTERVAL:
        with contextlib.suppress(DatabaseError):
            PersonalAccessToken.objects.filter(id=record.id).update(last_used_at=now)
    return record

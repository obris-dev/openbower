"""The account's sync key: the API key the account's feeds read signals
with when no person is present. Minted ONLY through a person's session
(the identity service mints a key as its user), stored encrypted, one per
account, and replaced by a reconnect once the service refuses it."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from auth_client.services.api_keys import ApiKeyService

from ..models import SignalsSyncKey
from .signals_client import KeySignalsClient, SignalsClientService

# The key's name in the person's API keys list: what it is for, so a person
# who revokes it there knows what stops.
SYNC_KEY_NAME = "Signals feed (sync)"
SYNC_KEY_SCOPES = ("signals:read",)


class SyncKeyService:
    """One account's sync key."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def ensure(self, session) -> SignalsSyncKey:
        """The account's working key, minting one through `session` when
        there is none or the service has refused it. Two first calls at
        once (two tabs creating a feed) end with one key: the unique row
        decides, and the loser revokes the key it minted."""
        with transaction.atomic():
            current = SignalsSyncKey.objects.select_for_update().filter(account_id=self.account_id).first()
            if current is not None:
                if current.rejected_at is None:
                    return current
                return self._replace(current, session)
        minted = ApiKeyService(session).mint(SYNC_KEY_NAME, scopes=SYNC_KEY_SCOPES)
        try:
            with transaction.atomic():
                return SignalsSyncKey.objects.create(
                    account_id=self.account_id,
                    user_id=session.user_id,
                    key_id=minted.key.id,
                    token=minted.token,
                )
        except IntegrityError:
            ApiKeyService(session).revoke(minted.key.id)
            return SignalsSyncKey.objects.get(account_id=self.account_id)

    def reconnect(self, session) -> SignalsSyncKey:
        """A fresh key through `session`, replacing the account's (revoked
        first, so a key that still works is not left behind), or the
        account's first when it has none."""
        with transaction.atomic():
            current = SignalsSyncKey.objects.select_for_update().filter(account_id=self.account_id).first()
            if current is not None:
                return self._replace(current, session)
        return self.ensure(session)

    def _replace(self, current: SignalsSyncKey, session) -> SignalsSyncKey:
        """Revoke the stored key and mint its successor into the same row.
        Called under the row's lock, so two replacements serialize."""
        keys = ApiKeyService(session)
        keys.revoke(current.key_id)
        minted = keys.mint(SYNC_KEY_NAME, scopes=SYNC_KEY_SCOPES)
        current.user_id = session.user_id
        current.key_id = minted.key.id
        current.token = minted.token
        current.rejected_at = None
        current.save(update_fields=["user_id", "key_id", "token", "rejected_at", "updated_at"])
        return current

    def client(self) -> KeySignalsClient | None:
        """The feed's signals client off the account's key, or None when the
        account has no key or the service refused it (its feeds wait for a
        reconnect)."""
        current = SignalsSyncKey.objects.filter(account_id=self.account_id, rejected_at__isnull=True).first()
        if current is None:
            return None
        return SignalsClientService.for_key(current.token)

    def mark_rejected(self) -> None:
        """The service refused the account's key: stop using it until a
        reconnect."""
        SignalsSyncKey.objects.filter(account_id=self.account_id, rejected_at__isnull=True).update(
            rejected_at=timezone.now()
        )

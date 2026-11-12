"""The account's sync key: the API key the account's signals feeds read
with when no person is present. Minted ONLY through a person's session
(the identity service mints a key as its user), stored encrypted, one per
account, and replaced by a reconnect once the service refuses it.

Every identity-service call runs OUTSIDE a transaction: a call can spend
the session's refresh token, and a rollback around it would restore a
token the service has already spent (the person's next request would log
them out). So a replacement mints first, swaps the row in one short
compare-and-set, and only then retires the key it replaced."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from auth_client.models import AppSession
from auth_client.services.api_keys import ApiKeyRefused, ApiKeyService
from auth_client.services.oauth.errors import AuthUpstreamError
from openbower_schema.auth import ApiKeyMinted

from ..constants import SYNC_KEY_SCOPES
from ..models import SignalsSyncKey
from .signals_client import KeySignalsClient, SignalsClientService

logger = logging.getLogger(__name__)

# The key's name in the person's API keys list: what it is for, so a person
# who revokes it there knows what stops.
SYNC_KEY_NAME = "Signals feed (sync)"


class SyncKeyAccountMismatch(Exception):
    """A session from another account: its key would act for that account."""


@dataclass(frozen=True)
class SyncKeyReplaced:
    """What a replacement did. `left_live_key_id` names the key it replaced
    when that key is another member's: only its owner can revoke it, so it
    stays live in their API keys list until they do (the caller says so)."""

    key: SignalsSyncKey
    left_live_key_id: str = ""
    left_live_user_id: str = ""


class _LostRace(Exception):
    """The row moved between the read and the swap: another replacement won."""


class SyncKeyService:
    """One account's sync key."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def ensure(self, session: AppSession) -> SignalsSyncKey:
        """The account's working key, minting one through `session` (a
        person of this account) when there is none or the service has
        refused it."""
        self._require_own(session)
        current = SignalsSyncKey.objects.filter(account_id=self.account_id).first()
        if current is not None and current.rejected_at is None:
            return current
        return self._swap(session, expected=current).key

    def reconnect(self, session: AppSession) -> SyncKeyReplaced:
        """A fresh key through `session` (a person of this account) in place
        of the account's (or its first, when it has none), the replaced key
        retired after."""
        self._require_own(session)
        current = SignalsSyncKey.objects.filter(account_id=self.account_id).first()
        return self._swap(session, expected=current)

    def _require_own(self, session: AppSession) -> None:
        if session.account_id != self.account_id:
            raise SyncKeyAccountMismatch(f"a session of account {session.account_id} for {self.account_id}")

    def _swap(self, session, *, expected: SignalsSyncKey | None) -> SyncKeyReplaced:
        """Mint, then make the minted key the account's in one short write:
        a create when the account had none, else a compare-and-set on the
        key it had. A rival that got there first wins; this mint is revoked
        and the account's key as it now stands comes back."""
        keys = ApiKeyService(session)
        minted = keys.mint(SYNC_KEY_NAME, scopes=SYNC_KEY_SCOPES)
        try:
            with transaction.atomic():
                key = self._install(session, minted, expected=expected)
        except (IntegrityError, _LostRace):
            keys.revoke(minted.key.id)
            return SyncKeyReplaced(key=SignalsSyncKey.objects.get(account_id=self.account_id))
        if expected is None:
            return SyncKeyReplaced(key=key)
        return self._retire(keys, session, replaced=expected, key=key)

    def _install(self, session, minted: ApiKeyMinted, *, expected: SignalsSyncKey | None) -> SignalsSyncKey:
        if expected is None:
            return SignalsSyncKey.objects.create(
                account_id=self.account_id, user_id=session.user_id, key_id=minted.key.id, token=minted.token
            )
        swapped = SignalsSyncKey.objects.filter(pk=expected.pk, key_id=expected.key_id).update(
            user_id=session.user_id,
            key_id=minted.key.id,
            token=minted.token,
            rejected_at=None,
            updated_at=timezone.now(),
        )
        if not swapped:
            raise _LostRace
        return SignalsSyncKey.objects.get(pk=expected.pk)

    def _retire(
        self, keys: ApiKeyService, session, *, replaced: SignalsSyncKey, key: SignalsSyncKey
    ) -> SyncKeyReplaced:
        """Revoke the replaced key when this person owns it. Another member's
        is theirs alone to revoke (the identity service revokes only an
        owner's keys), so it is reported, not silently counted revoked."""
        if replaced.user_id != session.user_id:
            logger.warning(
                "sync key %s replaced for account %s; its owner %s must revoke it",
                replaced.key_id,
                self.account_id,
                replaced.user_id,
            )
            return SyncKeyReplaced(key=key, left_live_key_id=replaced.key_id, left_live_user_id=replaced.user_id)
        try:
            keys.revoke(replaced.key_id)
        except (ApiKeyRefused, AuthUpstreamError):
            # The new key is already the account's; the old one stays in the
            # owner's list until they revoke it, and is reported as such.
            logger.warning(
                "sync key %s replaced for account %s but its revoke failed", replaced.key_id, self.account_id
            )
            return SyncKeyReplaced(key=key, left_live_key_id=replaced.key_id, left_live_user_id=replaced.user_id)
        return SyncKeyReplaced(key=key)

    def client(self) -> KeySignalsClient | None:
        """The feed's signals client off the account's key, or None when the
        account has no key or the service refused it (its feeds wait for a
        person). The client names its key, so a refusal marks that key."""
        current = SignalsSyncKey.objects.filter(account_id=self.account_id, rejected_at__isnull=True).first()
        if current is None:
            return None
        return SignalsClientService.for_key(token=current.token, key_id=current.key_id)

    def mark_rejected(self, key_id: str) -> None:
        """The service refused `key_id`: stop using it. A key a reconnect has
        already replaced is left alone, so a call that was out when the
        reconnect landed never stops its successor."""
        SignalsSyncKey.objects.filter(account_id=self.account_id, key_id=key_id, rejected_at__isnull=True).update(
            rejected_at=timezone.now()
        )

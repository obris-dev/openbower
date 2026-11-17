"""A custom signal's webhook URL: the token in it, minted and rotated
through a person and read back by one to copy, and the push that presents
it resolved to its signal's sheet in one read with no session behind it."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from openbower_kernel.hashing import hash_token
from sheets.constants import SheetKind
from sheets.models import Sheet

from ..constants import WEBHOOK_TOKEN_BYTES, WEBHOOK_TOKENS_REVOKED_KEPT
from ..models import SignalSource, SourceToken
from ..sources.registry import WEBHOOK
from .logs import LogNotFound


class WebhookTokenUnreadable(Exception):
    """A live token whose stored copy no longer decrypts to it (the
    encryption key changed since it was minted): pushes still resolve by
    its hash, but the URL cannot be read back, so a rotate replaces it."""

    def __init__(self, source_id: str) -> None:
        super().__init__(
            "This webhook URL can't be shown anymore. Rotate URL to get a new one; pushes to it still work."
        )
        self.source_id = source_id


class NoLiveToken(Exception):
    """A webhook source with no live token: a broken invariant, said
    loudly (every create and rotate mints one)."""

    def __init__(self, source_id: str) -> None:
        super().__init__(f"webhook source {source_id} has no live token")
        self.source_id = source_id


@dataclass(frozen=True)
class WebhookGrant:
    """What a webhook URL's token opens: the signal's sheet, and the webhook
    source the token was minted for (what a push's own record of itself
    keys on)."""

    sheet: Sheet
    source_id: str


class WebhookTokenGlobal:
    """The webhook push's lookup, across every account: the URL carries no
    account, so its token names one."""

    @staticmethod
    def push_target(sheet_id: str, raw: str) -> WebhookGrant:
        """The signal's sheet and the token's source, when `raw` is its live
        token, in one read (the unique token_hash index). Raises LogNotFound
        for anything else, a wrong, revoked or another signal's token
        included, so the URL never says whether a signal exists."""
        token = SourceToken.objects.filter(sheet_id=OuterRef("id"), token_hash=hash_token(raw), revoked_at__isnull=True)
        token_source = token.values("source_id")[:1]
        signals = Sheet.objects.filter(id=sheet_id, kind=SheetKind.LOG)
        annotated = signals.annotate(token_source_id=Subquery(token_source))
        found = annotated.filter(token_source_id__isnull=False).first()
        if found is None:
            raise LogNotFound(sheet_id)
        return WebhookGrant(sheet=found, source_id=found.token_source_id)


class WebhookTokenService:
    """A custom signal's webhook tokens, in one account: minted through a
    person, read back by a signed-in person, and deleted with their signal."""

    Global = WebhookTokenGlobal

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def mint(self, source: SignalSource, *, user_id: str) -> str:
        """A fresh token for a webhook source, stored hashed and encrypted:
        the live one it replaces is revoked in the same transaction, so a
        signal has one working URL, and revoked ones past the newest few are
        deleted. The source row is locked so two rotates take turns rather
        than both minting. Raises LogNotFound when the webhook source
        is gone by the time the lock is taken (a delete got there first)."""
        raw = secrets.token_urlsafe(WEBHOOK_TOKEN_BYTES)
        now = timezone.now()
        with transaction.atomic():
            webhook = SignalSource.objects.filter(account_id=self.account_id, id=source.id, kind=WEBHOOK)
            locked = webhook.select_for_update().first()
            if locked is None:
                raise LogNotFound(source.sheet_id)
            live = SourceToken.objects.filter(account_id=self.account_id, source_id=source.id, revoked_at__isnull=True)
            live.update(revoked_at=now)
            SourceToken.objects.create(
                account_id=self.account_id,
                user_id=user_id,
                sheet_id=source.sheet_id,
                source_id=str(source.id),
                token_hash=hash_token(raw),
                token=raw,
                last4=raw[-4:],
            )
            # Under the same lock, so two rotates never prune each other's.
            revoked = SourceToken.objects.filter(
                account_id=self.account_id, sheet_id=source.sheet_id, revoked_at__isnull=False
            )
            newest_first = revoked.order_by("-revoked_at", "-id")
            past_kept = newest_first.values_list("id", flat=True)[WEBHOOK_TOKENS_REVOKED_KEPT:]
            stale_ids = list(past_kept)
            if stale_ids:
                stale = SourceToken.objects.filter(id__in=stale_ids)
                stale.delete()
        return raw

    def live_token(self, source: SignalSource) -> str:
        """The webhook source's live token, decrypted, for a signed-in
        person to copy its URL. A webhook source always has one (minted at
        its create, replaced at each rotate), so none is NoLiveToken; a
        source deleted since the caller read it is LogNotFound (a 404)."""
        source_id = str(source.id)
        live = SourceToken.objects.filter(account_id=self.account_id, source_id=source_id, revoked_at__isnull=True)
        found = live.first()
        if found is None:
            # A delete that committed after the caller read the signal took
            # its tokens too: the signal is gone, not broken.
            still_there = SignalSource.objects.filter(account_id=self.account_id, id=source_id)
            if not still_there.exists():
                raise LogNotFound(source.sheet_id)
            raise NoLiveToken(source_id)
        # The field hands back what it cannot decrypt unchanged, so a key
        # change would otherwise read as a URL that 404s.
        token = found.token
        if hash_token(token) != found.token_hash:
            raise WebhookTokenUnreadable(source_id)
        return token

    def delete_for(self, sheet_id: str) -> None:
        """Every token of a signal being deleted, revoked ones included:
        called inside the log's delete, which holds the transaction."""
        tokens = SourceToken.objects.filter(account_id=self.account_id, sheet_id=sheet_id)
        tokens.delete()

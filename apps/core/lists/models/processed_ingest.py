from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import AccountScopedModel

from ..constants import MAX_INGEST_EVENT_ID_LENGTH


class ProcessedIngestEvent(AccountScopedModel):
    """The idempotent-consumer inbox: one row per accepted push the worker
    has handled, so an at-least-once redelivery is SKIPPED instead of
    re-applied. Written in the same transaction as the apply, which is what
    makes dedup correct (a cache cannot, since it can't share that
    transaction). ACCOUNT is the dedupe scope (AccountScopedModel), not user:
    a caller-chosen event_id is not global, so the same key from two tenants
    must not collide, but there is no per-user attribution on a worker
    ledger. Pruned to a recent window (a redelivery only happens for so
    long); not a permanent ledger.
    """

    event_id = models.CharField(_("event id"), max_length=MAX_INGEST_EVENT_ID_LENGTH)
    list_id = models.CharField(_("list id"), max_length=26)

    class Meta:
        verbose_name = _("processed ingest event")
        verbose_name_plural = _("processed ingest events")
        # The unique (account_id, event_id) index also serves the dedupe
        # lookup, so account_id earns no separate index here.
        constraints = [
            models.UniqueConstraint(fields=["account_id", "event_id"], name="uniq_processed_ingest_account_event"),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} (account {self.account_id})"

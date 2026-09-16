"""Account-scoped custody of webhook deliveries: the log of every POST
attempt, its per-destination reads (the newest row is a destination's
health; the page is its log), and its cleanup. System-level
operations (the prune the cron ticks) live under `WebhookDeliveryGlobal`,
exposed as `WebhookDeliveryService.Global`, the sessions service's split."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from openbower_kernel.batches import iter_id_pages
from openbower_kernel.fields import min_ulid_at

from ..constants import (
    DELIVERY_WRITE_BATCH,
    WEBHOOK_DELIVERY_MAX_AGE_SECONDS,
    WEBHOOK_ERROR_MAX_LENGTH,
    WebhookEnvelopeType,
)
from ..delivery.protocol import DeliveryResult
from ..models import WebhookDelivery


class WebhookDeliveryGlobal:
    """Global on purpose (a schedule has no account), unlike every
    request path on the service below, so it must be safe against every
    account's live traffic: it judges age alone."""

    @staticmethod
    def prune(*, older_than_seconds: int = WEBHOOK_DELIVERY_MAX_AGE_SECONDS) -> int:
        """Delete deliveries older than the retention. Age is the ULID
        birth. A delivery has no children, so each page is one delete.
        Returns the purge count."""
        cutoff = min_ulid_at(timezone.now() - timedelta(seconds=older_than_seconds))
        purged = 0
        for page in iter_id_pages(WebhookDelivery.objects.filter(id__lt=cutoff), batch=DELIVERY_WRITE_BATCH):
            WebhookDelivery.objects.filter(id__in=page).delete()
            purged += len(page)
        return purged


class WebhookDeliveryService:
    Global = WebhookDeliveryGlobal

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def record(
        self,
        destination_id: str,
        *,
        envelope_type: WebhookEnvelopeType,
        test: bool,
        result: DeliveryResult,
        delivery_id: str,
    ) -> WebhookDelivery:
        """The delivery row, `id` being the webhook-id the request
        carried. Recorded whatever became of the destination meanwhile:
        the request went out, so the log says so; a row whose
        destination was deleted in the same instant is swept by the
        prune."""
        return WebhookDelivery.objects.create(
            id=delivery_id,
            account_id=self.account_id,
            destination_id=destination_id,
            type=envelope_type,
            test=test,
            status=result.status,
            http_status=result.http_status,
            duration_ms=result.duration_ms,
            error=result.error[:WEBHOOK_ERROR_MAX_LENGTH],
            response_excerpt=result.response_excerpt,
        )

    def newest_for(self, destination_ids: list[str]) -> dict[str, WebhookDelivery]:
        """The newest delivery per destination (its health), keyed by
        destination id, in one query: DISTINCT ON the destination with
        the newest id first, which the (destination_id, id) index serves
        in order. Ids are ULIDs, so the newest id is the newest row."""
        if not destination_ids:
            return {}
        rows = (
            WebhookDelivery.objects.filter(account_id=self.account_id, destination_id__in=destination_ids)
            .order_by("destination_id", "-id")
            .distinct("destination_id")
        )
        return {row.destination_id: row for row in rows}

    def page_for(self, destination_id: str, *, after_id: str, limit: int) -> list[WebhookDelivery]:
        qs = WebhookDelivery.objects.filter(account_id=self.account_id, destination_id=destination_id).order_by("-id")
        if after_id:
            qs = qs.filter(id__lt=after_id)
        return list(qs[:limit])

    def delete_for(self, destination_id: str) -> int:
        """A destination's whole log, for the owner's delete (no
        cascades). Returns the count."""
        deleted, _ = WebhookDelivery.objects.filter(account_id=self.account_id, destination_id=destination_id).delete()
        return deleted

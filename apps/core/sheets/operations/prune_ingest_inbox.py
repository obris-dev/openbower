"""The runnable: PruneIngestInboxOperation().run() -> purge count.

Scheduled maintenance, what the compose cron ticks: global on purpose
(a schedule has no account), so it judges age alone. The inbox holds
only which pushes were applied, never their rows, so a pruned entry
loses nothing but the dedupe of a redelivery older than the bus keeps."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from openbower_kernel.batches import iter_id_pages
from openbower_kernel.fields import min_ulid_at

from ..constants import INGEST_INBOX_MAX_AGE_SECONDS, INGEST_INBOX_WRITE_BATCH
from ..models import ProcessedIngestEvent


class PruneIngestInboxOperation:
    def run(self) -> int:
        """Delete inbox entries older than the retention. Age is the ULID
        birth. Returns the purge count for the command's log."""
        cutoff = min_ulid_at(timezone.now() - timedelta(seconds=INGEST_INBOX_MAX_AGE_SECONDS))
        purged = 0
        old = ProcessedIngestEvent.objects.filter(id__lt=cutoff)
        for page in iter_id_pages(old, batch=INGEST_INBOX_WRITE_BATCH):
            ProcessedIngestEvent.objects.filter(id__in=page).delete()
            purged += len(page)
        return purged

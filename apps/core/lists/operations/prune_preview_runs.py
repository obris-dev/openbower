"""The runnable: PrunePreviewRunsOperation().run() -> purge count.

Scheduled maintenance, what the compose cron ticks: global on purpose
(a schedule has no account), unlike every request service, so it must
be safe against EVERY account's live traffic, which is why it judges
age alone with a bound generous next to any poll."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from openbower_kernel.batches import iter_id_pages
from openbower_kernel.fields import min_ulid_at

from ..constants import FILL_WRITE_BATCH, PREVIEW_RUN_MAX_AGE_SECONDS
from ..models import NodeRun
from ..services.preview_runs import preview_runs


class PrunePreviewRunsOperation:
    def run(self) -> int:
        """Delete preview runs older than the baseline, whatever their
        status (at a day old even a "live" one is an orphan no poll is
        watching). A preview run's whole child set is itself: no cell
        truth, no sheet rows, no fill. Age is the ULID birth. Returns
        the purge count for the command's log."""
        cutoff = min_ulid_at(timezone.now() - timedelta(seconds=PREVIEW_RUN_MAX_AGE_SECONDS))
        purged = 0
        for page in iter_id_pages(preview_runs().filter(id__lt=cutoff), batch=FILL_WRITE_BATCH):
            NodeRun.objects.filter(id__in=page).delete()
            purged += len(page)
        return purged

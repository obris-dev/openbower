"""The runnable: SweepTestFillsOperation().run() -> purge count.

Scheduled maintenance, what the compose cron ticks: global on purpose
(a schedule has no account), unlike every request service, so it must
be safe against EVERY account's live traffic, which is why it judges
age alone with a bound generous next to any poll."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from openbower_kernel.batches import iter_id_pages
from openbower_kernel.fields import min_ulid_at

from ..constants import FILL_WRITE_BATCH, TEST_FILL_MAX_AGE_SECONDS, FillKind
from ..models import Fill, NodeRun


class SweepTestFillsOperation:
    def run(self) -> int:
        """Delete test-kind fills older than the baseline, tasks first
        (no cascades: the owner deletes its own children, or they are
        orphaned forever; a test fill's whole child set IS its tasks:
        no cell truth, no sheet rows, no ephemeral agent). Age is the
        ULID birth; status is deliberately not consulted, because at a
        day old even a "live" test is an orphan no poll is watching.
        Each page's two deletes ride one transaction so a crash between
        them cannot orphan tasks. Returns the purge count for the
        command's log."""
        cutoff = min_ulid_at(timezone.now() - timedelta(seconds=TEST_FILL_MAX_AGE_SECONDS))
        purged = 0
        for page in iter_id_pages(Fill.objects.filter(kind=FillKind.TEST, id__lt=cutoff), batch=FILL_WRITE_BATCH):
            with transaction.atomic():
                NodeRun.objects.filter(fill_run_id__in=page).delete()
                Fill.objects.filter(id__in=page).delete()
            purged += len(page)
        return purged

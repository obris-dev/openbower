"""The manual (fill-backed) lane's pick: each live fill's READY tasks in
one flushed page PER PASS, bounded by FILL_PUBLISH_BATCH. Offering every
live fill the same per-pass batch is the fairness point, a wide fill
cannot flood the bus ahead of a smaller one beside it in a pass, and no
per-fill rotation is needed because every pass offers every fill the same
batch. It bounds a pass, not the standing queue (the consumers drain at
their own rate). The shared loop, publish, producer, and heartbeat live
in `base`; only the pick differs."""

from __future__ import annotations

from pathlib import Path

from ...constants import FILL_PROVISION_IDLE_SECONDS, FILL_PUBLISH_BATCH, FillKind
from ...ingest.topics import FILL_TASKS, TEST_TASKS
from ...services import fill_progress
from ...services.fill_tasks import FillTaskFlow
from .base import ProvisionOperation


class FillProvisionOperation(ProvisionOperation):
    _HEARTBEAT_PATH = Path("/tmp/fill_provisioner.heartbeat")
    _IDLE_SECONDS = FILL_PROVISION_IDLE_SECONDS
    _LANE = "fill"

    def _one_pass(self) -> bool:
        worked = False
        for fill in fill_progress.iter_live_fills():
            if self.stop.is_set():
                break
            # Beat PER FILL, not just once per pass: walking every live fill
            # and blocking on its publish's ack is the slow part, so a wide
            # account would otherwise look WEDGED mid-pass.
            self._touch_heartbeat()
            # Route by kind: a TEST (bench) fill rides its own isolated
            # topic so it never queues behind a wide manual fill.
            topic = TEST_TASKS if fill.kind == FillKind.TEST else FILL_TASKS
            page = list(FillTaskFlow.iter_ready_for_fill(str(fill.id), limit=FILL_PUBLISH_BATCH))  # bounded per pass
            if not page:
                continue
            self._publish_batch(page, topic)  # one flush; durable BEFORE the marks; raises on a blip
            for task in page:
                FillTaskFlow.mark_queued(task)
            worked = True
        return worked

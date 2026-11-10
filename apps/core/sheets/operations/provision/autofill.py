"""The autofill lane's pick: READY null-run tasks, GLOBALLY, one flushed
page per pass, routed by what each run is: a preview run (the builder's
Test, owning its input) to the preview topic, a pushed row's run to the
autofill topic. One pick, two buses, so a watched one-row diagnostic
never queues behind the firehose. The shared loop, publish, producer,
and heartbeat live in `base`; only the pick and the routing differ."""

from __future__ import annotations

from pathlib import Path

from ...constants import AUTOFILL_PUBLISH_BATCH, AUTOFILL_WORKER_IDLE_SECONDS
from ...ingest.topics import AUTOFILL_RUNS, PREVIEW_RUNS
from ...services.node_runs import NodeRunFlow
from .base import ProvisionOperation


class AutofillProvisionOperation(ProvisionOperation):
    _HEARTBEAT_PATH = Path("/tmp/autofill_provisioner.heartbeat")
    _IDLE_SECONDS = AUTOFILL_WORKER_IDLE_SECONDS
    _LANE = "autofill"

    def _one_pass(self) -> bool:
        if self.stop.is_set():
            return False
        page = list(NodeRunFlow.iter_ready(limit=AUTOFILL_PUBLISH_BATCH))  # bounded by limit
        if not page:
            return False
        previews = [task for task in page if task.is_preview]
        rows = [task for task in page if not task.is_preview]
        # Durable on the bus BEFORE the marks; a blip raises and the page
        # stays READY for the next pass.
        if previews:
            self._publish_batch(previews, PREVIEW_RUNS)
        if rows:
            self._publish_batch(rows, AUTOFILL_RUNS)
        for task in page:
            NodeRunFlow.mark_queued(task)
        return True

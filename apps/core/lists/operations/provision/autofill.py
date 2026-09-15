"""The autofill lane's pick: READY null-run tasks, GLOBALLY, one flushed
page per pass. The shared loop, publish, producer, and heartbeat live in
`base`; only the pick differs."""

from __future__ import annotations

from pathlib import Path

from ...constants import AUTOFILL_PUBLISH_BATCH, AUTOFILL_WORKER_IDLE_SECONDS
from ...ingest.topics import AUTOFILL_RUNS
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
        self._publish_batch(page, AUTOFILL_RUNS)  # durable on the bus BEFORE the marks; raises on a blip
        for task in page:
            NodeRunFlow.mark_queued(task)
        return True

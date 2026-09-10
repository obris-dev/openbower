"""The autofill worker's LOOP: drain the autofill queue, one row at a
time, oldest first. The process (signals, lifecycle) lives in the
management command; this is the supervisor it drives, the way the fill
worker's supervisor lives beside its command.

Running the row's AI columns is NOT done here yet: this pass logs the
due row and clears the task, so the queue and its drain are provable
end to end before a metered run_cell and the cell-ledger write land (a
separate work stream). State lives in the queue, never in this process,
so a restart resumes from whatever tasks remain.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.db import DatabaseError

from ..constants import AUTOFILL_CLAIM_BATCH, AUTOFILL_WORKER_IDLE_SECONDS
from ..models import AutofillTask

logger = logging.getLogger(__name__)

# Liveness marker the loop refreshes every pass; the compose healthcheck
# marks the worker unhealthy when it goes stale, catching a WEDGED loop
# without false-alarming on an empty queue (an idle worker still loops
# and touches this). A fixed path so the healthcheck can name it; keep
# the two in sync.
_HEARTBEAT_PATH = Path("/tmp/autofill_worker.heartbeat")


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError as e:
        # A failed heartbeat is itself the unhealthy signal; never crash
        # the loop over it.
        logger.warning("autofill heartbeat write failed: %s", e)


class AutofillWorkerOperation:
    """The drain loop. One instance per worker process; state is the
    queue, so a restart or a crash loses nothing but in-flight progress
    (which the next pass re-derives from the tasks that survive)."""

    def __init__(self, *, worker_id: str, stop) -> None:
        self.worker_id = worker_id
        self.stop = stop

    def run(self, once: bool = False) -> None:
        while not self.stop.is_set():
            _touch_heartbeat()  # the loop is turning, empty queue or not
            try:
                drained = self._one_pass()
            except DatabaseError as e:
                # Transient (a restart mid-connection, a lock timeout):
                # idle and retry rather than crash, matching the fill
                # worker's loop. A task the pass did not reach survives.
                logger.warning("autofill pass hit a database error, retrying: %s", e)
                self.stop.wait(AUTOFILL_WORKER_IDLE_SECONDS)
                continue
            if drained == 0:
                if once:
                    break
                # Interruptible sleep: SIGTERM sets the stop event, which
                # wakes this immediately for a prompt drain.
                self.stop.wait(AUTOFILL_WORKER_IDLE_SECONDS)

    def _one_pass(self) -> int:
        """Process up to one batch, oldest first. Returns how many tasks
        were handled, so the caller idles only on a truly empty queue."""
        tasks = list(AutofillTask.objects.order_by("id")[:AUTOFILL_CLAIM_BATCH])
        for task in tasks:
            if self.stop.is_set():
                # Drain: stop taking new work and let the process exit;
                # the untouched tasks stay queued for the next start.
                break
            self._process(task)
        return len(tasks)

    def _process(self, task: AutofillTask) -> None:
        # The row was just appended, so it is empty and owed a fill;
        # running its AI columns is the next work stream. Log the due
        # row and clear the task so the queue drains.
        logger.info(
            "autofill due row=%s list=%s account=%s",
            task.row_id,
            task.list_id,
            task.account_id,
        )
        AutofillTask.objects.filter(id=task.id).delete()

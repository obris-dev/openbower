"""The jobs PROCESS loop: tick, idle when nothing was due, tick again,
until told to stop. The provisioners' shape (operations/provision/
base.py::run), so the container is one more of the same kind: a
heartbeat file touched each pass (the compose healthcheck reads it: a
marker older than the window means the loop WEDGED, not that it is
idle), SIGTERM finishing the tick in flight, a database error idled
and retried rather than crashed. Restart-surviving by construction:
state lives on the job rows.

`once` runs a single tick and returns: the cron shape and the CI
smoke, so the same command serves both housings."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.db import DatabaseError

from ..constants import JOB_LOOP_IDLE_SECONDS
from .runner import JobRunner, TickReport

logger = logging.getLogger(__name__)

# A fixed path so the healthcheck can name it; keep the two in sync.
HEARTBEAT_PATH = Path("/tmp/run_jobs.heartbeat")


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_PATH.touch()
    except OSError as e:
        logger.warning("run_jobs heartbeat write failed: %s", e)


def run_loop(runner: JobRunner, *, stop: threading.Event, once: bool = False) -> None:
    """Tick until stopped. A tick that touched no job idles for
    JOB_LOOP_IDLE_SECONDS (the stop event wakes it); one that worked
    goes straight round, so a backlog drains at full speed."""
    while not stop.is_set():
        _touch_heartbeat()
        try:
            report = runner.tick()
        except DatabaseError as e:
            # Transient (a restart mid-connection, a lock timeout): idle
            # and retry rather than crash, like the workers.
            logger.warning("run_jobs hit a database error, retrying: %s", e)
            if once:
                return
            stop.wait(JOB_LOOP_IDLE_SECONDS)
            continue
        _log(report)
        if once:
            return
        if report.claimed == 0 and report.reclaimed == 0:
            stop.wait(JOB_LOOP_IDLE_SECONDS)


def _log(report: TickReport) -> None:
    if report.claimed or report.reclaimed:
        logger.info(
            "run_jobs: reclaimed=%d claimed=%d done=%d parked=%d failed=%d",
            report.reclaimed,
            report.claimed,
            report.done,
            report.parked,
            report.failed,
        )

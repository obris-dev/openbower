"""The loop a standing worker runs: take one unit of work, do it, and take the
next at once; with nothing due, sleep the idle and ask again. Each command's
step does the unit and writes its line, so the operation knows its unit and
this owns the process.

SIGTERM/SIGINT finish the unit in flight and exit. The heartbeat file is
touched before every unit; the liveness probe reads its age, so only a loop
wedged inside one unit (longer than any unit's own timeouts) is restarted. A
loop whose units run elsewhere (a pool) passes healthy, and the heartbeat is
withheld while it is False, so a wedged pool is restarted too.

A database error is logged and the loop goes on: a unit's state is in
Postgres, and a claim nobody finishes is taken over. A daemon whose progress
lives elsewhere (a consumer's Kafka offsets) must not let one reach here.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from pathlib import Path

from django.core.management.base import OutputWrapper
from django.db import DatabaseError, close_old_connections

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self, *, heartbeat: Path, idle_seconds: int, stdout: OutputWrapper, stderr: OutputWrapper) -> None:
        self.heartbeat = heartbeat
        self.idle_seconds = idle_seconds
        self.stdout = stdout
        self.stderr = stderr
        self.stopping = False

    def run(self, step: Callable[[], bool], *, once: bool, healthy: Callable[[], bool] = lambda: True) -> None:
        """step() does one unit of work and reports it; it returns whether it did
        work (False: nothing was due). once: work until nothing is due, then
        return. healthy() False withholds the heartbeat."""
        previous = {signum: signal.signal(signum, self._stop) for signum in (signal.SIGTERM, signal.SIGINT)}
        try:
            self._loop(step, once=once, healthy=healthy)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        if self.stopping:
            self.stdout.write("stopped on a signal; the units in flight finish first")

    def _loop(self, step: Callable[[], bool], *, once: bool, healthy: Callable[[], bool]) -> None:
        withheld = False
        while not self.stopping:
            if healthy():
                withheld = False
                self._touch()
            elif not withheld:
                withheld = True
                self.stderr.write("a unit has run past its limit; heartbeat withheld for the liveness probe")
            # except*: a step may fail several ways at once (the drain's units, as
            # one group). Its database errors are logged and the loop goes on;
            # anything else in the group re-raises by itself and stops the worker,
            # so a systemic failure is never hidden behind a database error.
            carry_on = False
            try:
                did_work = step()
            except* DatabaseError as group:
                for e in group.exceptions:
                    self.stderr.write(f"database error ({type(e).__name__}: {e}); continuing")
                carry_on = True
            if carry_on:
                close_old_connections()
                time.sleep(self.idle_seconds)
                continue
            if did_work:
                continue
            if once:
                return
            time.sleep(self.idle_seconds)

    def _stop(self, signum, frame) -> None:
        # Only the flag: a signal handler interrupts whatever the loop was
        # doing, a write to the same stream among it.
        self.stopping = True

    def _touch(self) -> None:
        try:
            self.heartbeat.touch()
        except OSError as e:
            # A failed heartbeat is itself the unhealthy signal: the probe sees it age.
            logger.warning("heartbeat write failed (%s): %s", self.heartbeat, e)

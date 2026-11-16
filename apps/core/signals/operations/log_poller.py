"""The log poller's unit: claim the due source that has waited longest,
read one page into its log, hand it back due again. The command runs it
as a kernel Daemon (openbower_kernel.daemon): one step after another
while sources are due, the idle when none is, a heartbeat before every
step, a stop between steps, and a database error logged and passed. Any
number run at once (the compose `log-poller` service, a Deployment's
replicas): a claim is a compare-and-set lease on the source, so two never
hold one source, and a dead poller's lease simply lapses.

FAIR BY DUE TIME. Every polled source is due at its own next_poll_at and
the due list is served longest waiting first. One step is one page (the
kind's poll), so a source with a backlog goes back behind every source
already waiting after each page rather than holding a poller until it
drains: a busy source never starves a quiet one. Accounts are not
weighed against each other (an account with many sources gets as many
turns); if that is ever needed, the due list orders by account
round-robin."""

from __future__ import annotations

import datetime
import logging

from django.db import DatabaseError
from django.utils import timezone

from ..constants import LOG_POLL_BACKOFF_SECONDS, LOG_POLL_CLAIM_CANDIDATES, LOG_POLL_LEASE_SECONDS
from ..models import SignalSource
from ..services.logs import LogService
from .poll_logs import PollOutcome, PollResult

logger = logging.getLogger(__name__)


class PollLogsOperation:
    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id

    def step(self, *, now: datetime.datetime | None = None) -> bool:
        """One source: the longest-waiting due source this poller can claim,
        polled and handed back. False when none is due, so the daemon
        idles. `now` fixes the clock (tests); otherwise each step reads it,
        so every source holds its full lease and is due again from when it
        was handed back."""
        at = now or timezone.now()
        for source_id in LogService.Global.due(at, limit=LOG_POLL_CLAIM_CANDIDATES):
            source = LogService.Global.claim(
                source_id, worker_id=self.worker_id, now=at, lease_seconds=LOG_POLL_LEASE_SECONDS
            )
            if source is not None:
                self._poll(source, at)
                return True
        return False

    def _poll(self, source: SignalSource, at: datetime.datetime) -> None:
        try:
            outcome = source.config.poll(source, at)
        except DatabaseError:
            raise
        except Exception:
            # One source's crash must not stop the sources behind it (the
            # jobs runner's rule; the daemon would stop the worker for it):
            # the cause is logged with the source's id, and the source backs
            # off rather than coming straight back as a poison pill.
            logger.exception("log poller: polling source %s crashed", source.id)
            again = at + datetime.timedelta(seconds=LOG_POLL_BACKOFF_SECONDS)
            outcome = PollOutcome(PollResult.CRASHED, next_poll_at=again)
        _log(source, outcome)
        if outcome.next_poll_at is not None:
            polled_at = at if outcome.read_through else None
            LogService.Global.release(
                str(source.id), worker_id=self.worker_id, next_poll_at=outcome.next_poll_at, polled_at=polled_at
            )


def _log(source: SignalSource, outcome: PollOutcome) -> None:
    # A source with nothing new is the common turn: said only when asked for.
    level = logging.DEBUG if outcome.result == PollResult.CAUGHT_UP else logging.INFO
    logger.log(level, "log poller: source %s %s rows=%d", source.id, outcome.result, outcome.rows)

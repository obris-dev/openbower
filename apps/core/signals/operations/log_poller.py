"""The log poller's unit: claim the due log that has waited longest, read
one page into it, hand it back due again. The command runs it as a kernel
Daemon (openbower_kernel.daemon): one step after another while logs are
due, the idle when none is, a heartbeat before every step, a stop between
steps, and a database error logged and passed. Any number run at once (the
compose `log-poller` service, a Deployment's replicas): a claim is a
compare-and-set lease on the log, so two never hold one log, and a dead
poller's lease simply lapses.

FAIR BY DUE TIME. Every log is due at its own next_poll_at and the due list
is served longest waiting first. One step is one page (PollLogOperation),
so a log with a backlog goes back behind every log already waiting after
each page rather than holding a poller until it drains: a busy log never
starves a quiet one. Accounts are not weighed against each other (an
account with many logs gets as many turns); if that is ever needed, the
due list orders by account round-robin."""

from __future__ import annotations

import datetime
import logging

from django.db import DatabaseError
from django.utils import timezone

from ..constants import LOG_POLL_BACKOFF_SECONDS, LOG_POLL_CLAIM_CANDIDATES, LOG_POLL_LEASE_SECONDS
from ..models import LogFeed
from ..services.logs import LogService
from .poll_logs import PollLogOperation, PollOutcome, PollResult

logger = logging.getLogger(__name__)


class PollLogsOperation:
    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id

    def step(self, *, now: datetime.datetime | None = None) -> bool:
        """One log: the longest-waiting due log this poller can claim,
        polled and handed back. False when none is due, so the daemon
        idles. `now` fixes the clock (tests); otherwise each step reads it,
        so every log holds its full lease and is due again from when it was
        handed back."""
        at = now or timezone.now()
        for feed_id in LogService.Global.due(at, limit=LOG_POLL_CLAIM_CANDIDATES):
            feed = LogService.Global.claim(
                feed_id, worker_id=self.worker_id, now=at, lease_seconds=LOG_POLL_LEASE_SECONDS
            )
            if feed is not None:
                self._poll(feed, at)
                return True
        return False

    def _poll(self, feed: LogFeed, at: datetime.datetime) -> None:
        try:
            outcome = PollLogOperation(feed=feed, now=at).run()
        except DatabaseError:
            raise
        except Exception:
            # One log's crash must not stop the logs behind it (the jobs
            # runner's rule; the daemon would stop the worker for it): the
            # cause is logged with the log's id, and the log backs off rather
            # than coming straight back as a poison pill.
            logger.exception("log poller: polling feed %s crashed", feed.id)
            again = at + datetime.timedelta(seconds=LOG_POLL_BACKOFF_SECONDS)
            outcome = PollOutcome(PollResult.CRASHED, next_poll_at=again)
        _log(feed, outcome)
        if outcome.next_poll_at is not None:
            polled_at = at if outcome.read_through else None
            LogService.Global.release(
                str(feed.id), worker_id=self.worker_id, next_poll_at=outcome.next_poll_at, polled_at=polled_at
            )


def _log(feed: LogFeed, outcome: PollOutcome) -> None:
    # A log with nothing new is the common turn: said only when asked for.
    level = logging.DEBUG if outcome.result == PollResult.CAUGHT_UP else logging.INFO
    logger.log(level, "log poller: feed %s %s rows=%d", feed.id, outcome.result, outcome.rows)

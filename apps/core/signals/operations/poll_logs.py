"""One page of one log's hosted source: read the listener's matches after
the source's cursor with the account's sync key, append them as rows, move
the cursor. Run by the poller (signals.operations.log_poller) for a source
it has claimed; no person is present, so the key is the only credential.
Each row carries its match's id as its external_id, so a page read twice
never doubles a row.

A page lands ONCE: the append and the cursor move are one transaction, and
the move is a compare-and-set from where the read started, so a second
poller that read the same page (a lease that lapsed under a slow one) rolls
its append back. The sheet is locked first (the append) and the source second
(the move), the order a log's delete takes them. No signals call runs
inside the transaction."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction

from sheets.constants import MAX_SHEET_ROWS, RowSource
from sheets.models import Sheet
from sheets.operations.append_rows import AppendRowsOperation
from sheets.rows import IncomingRow
from sheets.services.sheets import SheetFull, SheetNotFound

from ..constants import (
    LOG_POLL_BACKOFF_SECONDS,
    LOG_POLL_INTERVAL_SECONDS,
    LOG_POLL_PAGE,
    LOG_POLL_PARKED_SECONDS,
    LogStatus,
)
from ..log_columns import LOG_SHAPES
from ..models import SignalSource
from ..services.logs import LogNotFound, LogService, SourceOrphaned
from ..services.signals_client import (
    KeySignalsClient,
    SignalsClientRequestError,
    SignalsKeyRejected,
    SignalsNotFound,
    SignalsUpstreamError,
)
from ..services.sync_key import SyncKeyService
from ..sources.hosted import HostedSource

logger = logging.getLogger(__name__)


class PollResult(StrEnum):
    APPENDED = "appended"  # a page landed as rows
    CAUGHT_UP = "caught_up"  # nothing new since the cursor
    WAITING = "waiting"  # the account has no working sync key, or the key was just refused
    GONE = "gone"  # the listener no longer exists: the log leaves the queue
    FULL = "full"  # the sheet holds MAX_SHEET_ROWS rows
    DELETED = "deleted"  # the log was deleted while the page was out
    LOST = "lost"  # another poller landed this page first
    BACKOFF = "backoff"  # the signals service failed the read
    CRASHED = "crashed"  # the poll raised: the log backs off, the loop goes on


@dataclass(frozen=True)
class PollOutcome:
    result: PollResult
    # When the log is next due; None when it is gone from the queue (a
    # deleted log, a gone listener) and nothing is released.
    next_poll_at: datetime.datetime | None
    rows: int = 0

    @property
    def read_through(self) -> bool:
        """Whether this turn read the listener's matches to its end (a page
        landed, or nothing was new): what `last_polled_at` records."""
        return self.result in (PollResult.APPENDED, PollResult.CAUGHT_UP)


class _LostRace(Exception):
    """The cursor moved between the read and the landing: another poller
    landed this page."""


class PollLogOperation:
    def __init__(self, *, source: SignalSource, config: HostedSource, now: datetime.datetime) -> None:
        self.source = source
        self.config = config
        self.account_id = source.account_id
        self.now = now

    def run(self) -> PollOutcome:
        logs = LogService(account_id=self.account_id)
        try:
            sheet = logs.sheet_for(self.source)
        except LogNotFound:
            return PollOutcome(PollResult.DELETED, next_poll_at=None)
        except SourceOrphaned as e:
            # Said loudly and backed off, rather than reclaimed silently at
            # the front of the queue every lease.
            logger.error("%s", e)
            return self._again(PollResult.BACKOFF, LOG_POLL_BACKOFF_SECONDS)
        # The claimed source's word and the sheet's, not the log's other
        # sources': a poll is this source's turn.
        status = logs.status_of(sheet, (self.source,))
        if status == LogStatus.LISTENER_GONE:
            return PollOutcome(PollResult.GONE, next_poll_at=None)
        if status == LogStatus.RECONNECT:
            return self._again(PollResult.WAITING, LOG_POLL_PARKED_SECONDS)
        if status == LogStatus.FULL:
            return self._again(PollResult.FULL, LOG_POLL_PARKED_SECONDS)
        return self._read(logs, sheet)

    def _read(self, logs: LogService, sheet: Sheet) -> PollOutcome:
        source = self.source
        keys = SyncKeyService(account_id=self.account_id)
        client = keys.client_of(logs.current_key)
        if client is None:
            return self._again(PollResult.WAITING, LOG_POLL_PARKED_SECONDS)
        after = source.cursor or None
        try:
            page = client.listener_matches(listener_id=self.config.listener_id, after=after, limit=LOG_POLL_PAGE)
        except SignalsKeyRejected as e:
            keys.mark_rejected(e.key_id)
            return self._again(PollResult.WAITING, LOG_POLL_PARKED_SECONDS)
        except SignalsNotFound:
            return self._confirm_gone(client, keys, logs)
        except SignalsClientRequestError as e:
            # A refused read is a broken invariant, never guessed around: a
            # 400 is a cursor the service does not know (resetting it would
            # re-append every match), anything else a read it will not serve.
            if e.status == 400:
                logger.error("log %s: the signals service refused its cursor %r: %s", source.sheet_id, after, e)
            else:
                logger.error("log %s: the signals service refused its read (%s): %s", source.sheet_id, e.status, e)
            return self._again(PollResult.BACKOFF, LOG_POLL_BACKOFF_SECONDS)
        except SignalsUpstreamError as e:
            logger.warning("log %s: signals read failed (%s)", source.sheet_id, type(e).__name__)
            return self._again(PollResult.BACKOFF, LOG_POLL_BACKOFF_SECONDS)
        if not page.items:
            return self._again(PollResult.CAUGHT_UP, LOG_POLL_INTERVAL_SECONDS)
        return self._land(logs, sheet, page.items)

    def _confirm_gone(self, client: KeySignalsClient, keys: SyncKeyService, logs: LogService) -> PollOutcome:
        """A 404 from the matches read, confirmed on the listener itself
        before it is recorded: gone is final, so a 404 from anything in
        between (a gateway, a route an older service lacks) must not end
        the log."""
        try:
            client.listener(listener_id=self.config.listener_id)
        except SignalsNotFound:
            try:
                logs.mark_gone(self.source)
            except LogNotFound:
                return PollOutcome(PollResult.DELETED, next_poll_at=None)
            return PollOutcome(PollResult.GONE, next_poll_at=None)
        except SignalsKeyRejected as e:
            keys.mark_rejected(e.key_id)
            return self._again(PollResult.WAITING, LOG_POLL_PARKED_SECONDS)
        except (SignalsClientRequestError, SignalsUpstreamError) as e:
            logger.warning("log %s: confirming its listener failed (%s)", self.source.sheet_id, type(e).__name__)
            return self._again(PollResult.BACKOFF, LOG_POLL_BACKOFF_SECONDS)
        logger.error("log %s: its matches answered 404 while its listener is there", self.source.sheet_id)
        return self._again(PollResult.BACKOFF, LOG_POLL_BACKOFF_SECONDS)

    def _land(self, logs: LogService, sheet: Sheet, matches: list) -> PollOutcome:
        """What fits, oldest first: a sheet near its cap takes the first
        matches it has room for and the cursor stops at the last of them."""
        room = MAX_SHEET_ROWS - sheet.row_count
        landing = matches[:room]
        shape = LOG_SHAPES[self.config.type]
        rows = [IncomingRow(cells=shape.cells(match), external_id=match.id) for match in landing]
        append = AppendRowsOperation(
            account_id=self.account_id,
            target_sheet=sheet,
            rows=rows,
            source=RowSource.SIGNAL,
        )
        try:
            with transaction.atomic():
                report = append.run()
                if not logs.advance(self.source, to_cursor=landing[-1].id):
                    raise _LostRace()
        except _LostRace:
            return PollOutcome(PollResult.LOST, next_poll_at=self.now)
        except SheetNotFound:
            return PollOutcome(PollResult.DELETED, next_poll_at=None)
        except SheetFull:
            return self._again(PollResult.FULL, LOG_POLL_PARKED_SECONDS)
        # A full page means more is waiting: due again at once, behind every
        # log already waiting.
        more = len(matches) == LOG_POLL_PAGE and len(landing) == len(matches)
        delay = 0 if more else LOG_POLL_INTERVAL_SECONDS
        return self._again(PollResult.APPENDED, delay, rows=report.added)

    def _again(self, result: PollResult, seconds: int, *, rows: int = 0) -> PollOutcome:
        return PollOutcome(result, next_poll_at=self.now + datetime.timedelta(seconds=seconds), rows=rows)

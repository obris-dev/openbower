"""An account's logs as the app keeps them: a sheet of kind log and its
feed (the listener behind it). This is the local half of a log, written
together in one transaction; signals.operations.logs orders it against the
listener on the signals service."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from functools import cached_property

from django.db import models, transaction
from django.utils import timezone

from sheets.constants import MAX_SHEET_ROWS, SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.services.sheets import SheetNotFound, SheetService

from ..constants import LogStatus
from ..log_columns import LOG_SHAPES
from ..models import LogFeed, SignalsSyncKey
from .sync_key import SyncKeyService


class LogNotFound(Exception):
    """No log with that id in the account (a sheet that is not a log reads
    as missing too)."""


class LogFeedOrphaned(Exception):
    """A feed whose sheet is gone, or is no log: a broken invariant, since a
    log's create and delete write its sheet and feed together."""

    def __init__(self, feed_id: str, sheet_id: str) -> None:
        super().__init__(f"feed {feed_id}: its log {sheet_id} is gone or is not a log")
        self.feed_id = feed_id
        self.sheet_id = sheet_id


@dataclass(frozen=True)
class LocalLog:
    """The rows this app holds for a log: its sheet and its feed, and where
    it stands. Its listener lives on the signals service (`Log` carries
    both)."""

    sheet: Sheet
    feed: LogFeed
    status: LogStatus

    @property
    def sheet_id(self) -> str:
        """The log's id: its sheet's, as the routes take it."""
        return self.feed.sheet_id


class LogFeedGlobal:
    """The poller's queue across every account: which logs are due, and the
    lease that hands one to one poller at a time. Every write is a
    compare-and-set on the feed, so pollers need no lock between them."""

    @staticmethod
    def due(now: datetime.datetime, *, limit: int) -> list[str]:
        """Live logs due by `now` and not held, longest waiting first."""
        free = models.Q(leased_until__isnull=True) | models.Q(leased_until__lt=now)
        due = LogFeed.objects.filter(free, listener_gone_at__isnull=True, next_poll_at__lte=now)
        ordered = due.order_by("next_poll_at", "id")
        return [str(feed_id) for feed_id in ordered.values_list("id", flat=True)[:limit]]

    @staticmethod
    def claim(feed_id: str, *, worker_id: str, now: datetime.datetime, lease_seconds: int) -> LogFeed | None:
        """The log for this poller until the lease lapses, or None when
        another holds it or it is no longer due."""
        free = models.Q(leased_until__isnull=True) | models.Q(leased_until__lt=now)
        live = LogFeed.objects.filter(free, id=feed_id, listener_gone_at__isnull=True, next_poll_at__lte=now)
        until = now + datetime.timedelta(seconds=lease_seconds)
        if live.update(leased_until=until, leased_by=worker_id) != 1:
            return None
        return LogFeed.objects.filter(id=feed_id).first()

    @staticmethod
    def release(
        feed_id: str, *, worker_id: str, next_poll_at: datetime.datetime, polled_at: datetime.datetime | None
    ) -> None:
        """Hand the log back, due again at `next_poll_at`, and record
        `polled_at` when this turn read the listener through. Only the
        holder releases: a poller whose lease lapsed and was taken over
        writes nothing."""
        fields: dict = {"leased_until": None, "leased_by": "", "next_poll_at": next_poll_at}
        if polled_at is not None:
            fields["last_polled_at"] = polled_at
        held = LogFeed.objects.filter(id=feed_id, leased_by=worker_id)
        held.update(**fields)


class LogService:
    """One account's logs."""

    Global = LogFeedGlobal

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    @cached_property
    def sheets(self) -> SheetService:
        return SheetService(account_id=self.account_id)

    @cached_property
    def current_key(self) -> SignalsSyncKey | None:
        """The account's working sync key as this service first read it: one
        read per service, so a page of logs costs one, and a poll reads with
        the key its log's status was derived from."""
        keys = SyncKeyService(account_id=self.account_id)
        return keys.current()

    def _local(self, sheet: Sheet, feed: LogFeed) -> LocalLog:
        return LocalLog(sheet=sheet, feed=feed, status=self._status(sheet, feed))

    def _status(self, sheet: Sheet, feed: LogFeed) -> LogStatus:
        if feed.listener_gone_at is not None:
            return LogStatus.LISTENER_GONE
        if self.current_key is None:
            return LogStatus.RECONNECT
        if sheet.row_count >= MAX_SHEET_ROWS:
            return LogStatus.FULL
        return LogStatus.ACTIVE

    def get(self, sheet_id: str) -> LocalLog:
        try:
            sheet = self.sheets.get(sheet_id)
        except SheetNotFound as e:
            raise LogNotFound(sheet_id) from e
        feed = LogFeed.objects.filter(account_id=self.account_id, sheet_id=sheet_id).first()
        if sheet.kind != SheetKind.LOG or feed is None:
            raise LogNotFound(sheet_id)
        return self._local(sheet, feed)

    def for_feed(self, feed: LogFeed) -> LocalLog:
        """The log a feed already read fills (the poller's claim hands the
        row back fresh), so only its sheet is loaded. Raises LogNotFound when
        the log was deleted (its sheet and feed go together) and
        LogFeedOrphaned when the feed outlived its sheet."""
        try:
            sheet = self.sheets.get(feed.sheet_id)
        except SheetNotFound:
            sheet = None
        if sheet is not None and sheet.kind == SheetKind.LOG:
            return self._local(sheet, feed)
        still_fed = LogFeed.objects.filter(id=feed.id, account_id=self.account_id)
        if not still_fed.exists():
            raise LogNotFound(feed.sheet_id)
        raise LogFeedOrphaned(str(feed.id), feed.sheet_id)

    def page(self, *, after_id: str, limit: int) -> list[LocalLog]:
        """Newest first, keyset paged: the logs index. One feed read for the
        page, never one per log. Every log has exactly one feed (create and
        delete write both in one transaction), so a missing one is a broken
        invariant and raises."""
        sheets = self.sheets.page(kind=SheetKind.LOG, after_id=after_id, limit=limit)
        sheet_ids = [str(sheet.id) for sheet in sheets]
        rows = LogFeed.objects.filter(account_id=self.account_id, sheet_id__in=sheet_ids)
        feeds = {feed.sheet_id: feed for feed in rows}
        return [self._local(sheet, feeds[str(sheet.id)]) for sheet in sheets]

    def create(self, *, owner_id: str, label: str, signal: str, listener_id: str, autofill: bool) -> LocalLog:
        """A log's sheet, with its signal's base columns, and its feed."""
        shape = LOG_SHAPES[signal]
        with transaction.atomic():
            sheet = self.sheets.create(
                owner_id=owner_id,
                label=label,
                columns=list(shape.columns),
                origin=SheetOrigin.MANUAL,
                kind=SheetKind.LOG,
            )
            feed = LogFeed.objects.create(
                account_id=self.account_id,
                sheet_id=str(sheet.id),
                listener_id=listener_id,
                signal=signal,
                autofill=autofill,
            )
        return self._local(sheet, feed)

    def edit(self, local_log: LocalLog, *, label: str | None, autofill: bool | None) -> LocalLog:
        """A new label on the sheet, a new autofill on the feed, or both."""
        sheet = local_log.sheet
        feed = local_log.feed
        with transaction.atomic():
            if label is not None:
                sheet = self.sheets.rename_log(sheet, label=label)
            if autofill is not None and autofill != feed.autofill:
                feed.autofill = autofill
                feed.save(update_fields=["autofill", "updated_at"])
        return self._local(sheet, feed)

    def advance(self, feed: LogFeed, *, to_cursor: str) -> bool:
        """Move the log's cursor past the matches just appended, from where
        this read started and with the autofill it was appended under:
        False when another read moved the cursor first (a page lands once)
        or the person changed autofill meanwhile (the page lands again
        under the new setting, never queueing runs they just turned off),
        so the caller rolls back the rows it appended."""
        moved = LogFeed.objects.filter(
            id=feed.id, account_id=self.account_id, cursor=feed.cursor, autofill=feed.autofill
        )
        return moved.update(cursor=to_cursor) == 1

    def wake(self) -> int:
        """Make every live log of the account due at once: what a reconnect
        does, since its logs were parked waiting for a working key."""
        live = LogFeed.objects.filter(account_id=self.account_id, listener_gone_at__isnull=True)
        return live.update(next_poll_at=timezone.now())

    def mark_listener_gone(self, local_log: LocalLog) -> LocalLog:
        """The signals service has no such listener: recorded once (the
        first time wins) and final, so the log stops polling. Raises
        LogNotFound when the log itself is gone meanwhile (its delete
        removes the listener first, so a read in that window sees the 404
        before the rows go)."""
        feed = local_log.feed
        if feed.listener_gone_at is not None:
            return self._local(local_log.sheet, feed)
        sheet = local_log.sheet
        now = timezone.now()
        unmarked = LogFeed.objects.filter(id=feed.id, listener_gone_at__isnull=True)
        if unmarked.update(listener_gone_at=now) == 1:
            feed.listener_gone_at = now
            return self._local(sheet, feed)
        marked = LogFeed.objects.filter(id=feed.id).values_list("listener_gone_at", flat=True)
        gone_at = marked.first()
        if gone_at is None:
            raise LogNotFound(feed.sheet_id)
        feed.listener_gone_at = gone_at
        return self._local(sheet, feed)

    def delete(self, local_log: LocalLog) -> None:
        """The sheet first, then its feed: the order a feed's append takes
        them (add_rows locks the sheet, then the cursor moves), so a delete
        and an append wait for each other rather than deadlock."""
        sheet = local_log.sheet
        feed = local_log.feed
        with transaction.atomic():
            self.sheets.delete_log(sheet)
            feed.delete()

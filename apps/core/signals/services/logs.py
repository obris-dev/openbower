"""An account's logs as the app keeps them: a sheet of kind log and its
feed (the listener behind it). This is the local half of a log, written
together in one transaction; signals.operations.logs orders it against the
listener on the signals service."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from django.db import transaction

from sheets.constants import SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.services.sheets import SheetNotFound, SheetService

from ..log_columns import LOG_SHAPES
from ..models import LogFeed


class LogNotFound(Exception):
    """No log with that id in the account (a sheet that is not a log reads
    as missing too)."""


@dataclass(frozen=True)
class LocalLog:
    """The rows this app holds for a log: its sheet and its feed. Its
    listener lives on the signals service (`Log` carries both)."""

    sheet: Sheet
    feed: LogFeed


class LogService:
    """One account's logs."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    @cached_property
    def sheets(self) -> SheetService:
        return SheetService(account_id=self.account_id)

    def get(self, sheet_id: str) -> LocalLog:
        try:
            sheet = self.sheets.get(sheet_id)
        except SheetNotFound as e:
            raise LogNotFound(sheet_id) from e
        feed = LogFeed.objects.filter(account_id=self.account_id, sheet_id=sheet_id).first()
        if sheet.kind != SheetKind.LOG or feed is None:
            raise LogNotFound(sheet_id)
        return LocalLog(sheet=sheet, feed=feed)

    def page(self, *, after_id: str, limit: int) -> list[LocalLog]:
        """Newest first, keyset paged: the logs index. One feed read for the
        page, never one per log. Every log has exactly one feed (create and
        delete write both in one transaction), so a missing one is a broken
        invariant and raises."""
        sheets = self.sheets.page(kind=SheetKind.LOG, after_id=after_id, limit=limit)
        sheet_ids = [str(sheet.id) for sheet in sheets]
        rows = LogFeed.objects.filter(account_id=self.account_id, sheet_id__in=sheet_ids)
        feeds = {feed.sheet_id: feed for feed in rows}
        return [LocalLog(sheet=sheet, feed=feeds[str(sheet.id)]) for sheet in sheets]

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
        return LocalLog(sheet=sheet, feed=feed)

    def edit(self, local: LocalLog, *, label: str | None, autofill: bool | None) -> LocalLog:
        """A new label on the sheet, a new autofill on the feed, or both."""
        sheet = local.sheet
        feed = local.feed
        with transaction.atomic():
            if label is not None:
                sheet = self.sheets.rename_log(sheet, label=label)
            if autofill is not None and autofill != feed.autofill:
                feed.autofill = autofill
                feed.save(update_fields=["autofill", "updated_at"])
        return LocalLog(sheet=sheet, feed=feed)

    def delete(self, local: LocalLog) -> None:
        """The sheet first, then its feed: the order a feed's append takes
        them (add_rows locks the sheet, then the cursor moves), so a delete
        and an append wait for each other rather than deadlock."""
        with transaction.atomic():
            self.sheets.delete_log(local.sheet)
            local.feed.delete()

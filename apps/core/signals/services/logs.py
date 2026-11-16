"""An account's logs as the app keeps them: a sheet of kind log and its
sources (what feeds it: a hosted signal's listener, a custom signal's
webhook). This is the
local half of a log, written together in one transaction;
signals.operations.logs orders it against the listener on the signals
service."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from functools import cached_property
from typing import TypeVar

from django.db import models, transaction
from django.utils import timezone

from openbower_schema.sheets import PlainColumn
from sheets.constants import MAX_SHEET_ROWS, SheetKind, SheetOrigin
from sheets.models import Sheet
from sheets.services.columns import new_column_key
from sheets.services.sheets import SheetNotFound, SheetService

from ..constants import LogStatus
from ..log_columns import LOG_SHAPES
from ..models import SignalSource, SignalsSyncKey
from ..sources.base import SourceConfig
from ..sources.hosted import HostedSource
from ..sources.webhook import WebhookSource
from .sync_key import SyncKeyService

C = TypeVar("C", bound=SourceConfig)

# Where a log stands when its sources and its sheet each have a word: the
# first here that any of them says wins, and a log nothing speaks for is
# active. A status a kind says that is not ranked here refuses loudly.
_STATUS_ORDER: tuple[LogStatus, ...] = (
    LogStatus.LISTENER_GONE,
    LogStatus.RECONNECT,
    LogStatus.FULL,
    LogStatus.WAITING,
)


class LogNotFound(Exception):
    """No log with that id in the account (a sheet that is not a log reads
    as missing too)."""


class SourceOrphaned(Exception):
    """A source whose sheet is gone, or is no log: a broken invariant, since
    a log's create and delete write its sheet and sources together."""

    def __init__(self, source_id: str, sheet_id: str) -> None:
        super().__init__(f"source {source_id}: its log {sheet_id} is gone or is not a log")
        self.source_id = source_id
        self.sheet_id = sheet_id


class SourceMissing(Exception):
    """The log has no source of the kind a caller needs."""

    def __init__(self, sheet_id: str, kind: str) -> None:
        super().__init__(f"log {sheet_id} has no {kind} source")
        self.sheet_id = sheet_id
        self.kind = kind


@dataclass(frozen=True)
class LocalLog:
    """The rows this app holds for a log: its sheet, its sources, and where
    it stands. A hosted source's listener lives on the signals service
    (`Log` carries both)."""

    sheet: Sheet
    sources: tuple[SignalSource, ...]
    status: LogStatus
    # When its newest row arrived, whichever source brought it; None before
    # the first, or where the caller had no need to read it.
    last_received_at: datetime.datetime | None = None

    @property
    def sheet_id(self) -> str:
        """The log's id: its sheet's, as the routes take it."""
        return str(self.sheet.id)

    def find(self, kind: type[C]) -> tuple[SignalSource, C] | None:
        """The source of that kind and its config, typed as the kind's class;
        None when no such source feeds the log."""
        for source in self.sources:
            config = source.config
            if isinstance(config, kind):
                return source, config
        return None

    def require(self, kind: type[C]) -> tuple[SignalSource, C]:
        """As find, for a caller that cannot act without it: raises
        SourceMissing."""
        found = self.find(kind)
        if found is None:
            raise SourceMissing(self.sheet_id, kind.KIND)
        return found


def _free(now: datetime.datetime) -> models.Q:
    return models.Q(leased_until__isnull=True) | models.Q(leased_until__lt=now)


def _live_polled() -> models.Q:
    # A kind the poller never reads has no due time (SignalSource.save).
    return models.Q(gone_at__isnull=True, next_poll_at__isnull=False)


class SignalSourceGlobal:
    """The poller's queue across every account: which polled sources are
    due, and the lease that hands one to one poller at a time. Every write
    is a compare-and-set on the source, so pollers need no lock between
    them."""

    @staticmethod
    def due(now: datetime.datetime, *, limit: int) -> list[str]:
        """Live polled sources due by `now` and not held, longest waiting
        first."""
        free = _free(now)
        live = _live_polled()
        due = SignalSource.objects.filter(free, live, next_poll_at__lte=now)
        ordered = due.order_by("next_poll_at", "id")
        return [str(source_id) for source_id in ordered.values_list("id", flat=True)[:limit]]

    @staticmethod
    def claim(source_id: str, *, worker_id: str, now: datetime.datetime, lease_seconds: int) -> SignalSource | None:
        """The source for this poller until the lease lapses, or None when
        another holds it or it is no longer due."""
        free = _free(now)
        polled = _live_polled()
        live = SignalSource.objects.filter(free, polled, id=source_id, next_poll_at__lte=now)
        until = now + datetime.timedelta(seconds=lease_seconds)
        if live.update(leased_until=until, leased_by=worker_id) != 1:
            return None
        return SignalSource.objects.filter(id=source_id).first()

    @staticmethod
    def release(
        source_id: str, *, worker_id: str, next_poll_at: datetime.datetime, polled_at: datetime.datetime | None
    ) -> None:
        """Hand the source back, due again at `next_poll_at`, and record
        `polled_at` when this turn read it through. Only the holder
        releases: a poller whose lease lapsed and was taken over writes
        nothing."""
        fields: dict = {"leased_until": None, "leased_by": "", "next_poll_at": next_poll_at}
        if polled_at is not None:
            fields["last_polled_at"] = polled_at
        held = SignalSource.objects.filter(id=source_id, leased_by=worker_id)
        held.update(**fields)


@dataclass(frozen=True)
class DeclaredColumn:
    """A column a person declares on a custom signal: its key derives from
    its label, as every column's does."""

    label: str
    type: str


@dataclass(frozen=True)
class _LogContext:
    """What a source's status reads beyond its own row (sources.base)."""

    service: LogService
    sheet: Sheet

    @property
    def has_working_key(self) -> bool:
        return self.service.current_key is not None

    @property
    def has_rows(self) -> bool:
        return self.sheet.row_count > 0


class LogService:
    """One account's logs."""

    Global = SignalSourceGlobal

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

    def _sources(self, sheet_ids: list[str]) -> dict[str, tuple[SignalSource, ...]]:
        """Each log's sources, in one read. Every log has at least one (its
        create writes them with its sheet), so a log without is a broken
        invariant its caller raises on."""
        mine = SignalSource.objects.filter(account_id=self.account_id, sheet_id__in=sheet_ids)
        rows = mine.order_by("id")
        grouped: dict[str, list[SignalSource]] = {}
        for source in rows:
            sheet_sources = grouped.setdefault(source.sheet_id, [])
            sheet_sources.append(source)
        return {sheet_id: tuple(sources) for sheet_id, sources in grouped.items()}

    def _local(
        self, sheet: Sheet, sources: tuple[SignalSource, ...], *, last_received_at: datetime.datetime | None = None
    ) -> LocalLog:
        status = self.status_of(sheet, sources)
        return LocalLog(sheet=sheet, sources=sources, status=status, last_received_at=last_received_at)

    def status_of(self, sheet: Sheet, sources: tuple[SignalSource, ...]) -> LogStatus:
        """What the sources say and what the sheet says, ranked by
        _STATUS_ORDER."""
        context = _LogContext(service=self, sheet=sheet)
        said = {source.config.status(source, context) for source in sources}
        said.discard(None)
        if sheet.row_count >= MAX_SHEET_ROWS:
            said.add(LogStatus.FULL)
        unranked = said.difference(_STATUS_ORDER)
        if unranked:
            raise ValueError(f"log {sheet.id}: statuses {sorted(unranked)!r} have no rank in _STATUS_ORDER")
        return next((status for status in _STATUS_ORDER if status in said), LogStatus.ACTIVE)

    def get(self, sheet_id: str) -> LocalLog:
        try:
            sheet = self.sheets.get(sheet_id)
        except SheetNotFound as e:
            raise LogNotFound(sheet_id) from e
        found = self._sources([sheet_id])
        sources = found.get(sheet_id)
        if sheet.kind != SheetKind.LOG or sources is None:
            raise LogNotFound(sheet_id)
        arrivals = self.sheets.last_arrivals([sheet])
        return self._local(sheet, sources, last_received_at=arrivals.get(sheet_id))

    def sheet_for(self, source: SignalSource) -> Sheet:
        """The sheet a source the poller just claimed fills: the claim hands
        the source back fresh, so only the sheet is read. Raises LogNotFound
        when the log was deleted (its sheet and sources go together) and
        SourceOrphaned when the source outlived its sheet."""
        try:
            sheet = self.sheets.get(source.sheet_id)
        except SheetNotFound:
            sheet = None
        if sheet is not None and sheet.kind == SheetKind.LOG:
            return sheet
        still_there = SignalSource.objects.filter(id=source.id, account_id=self.account_id)
        if not still_there.exists():
            raise LogNotFound(source.sheet_id)
        raise SourceOrphaned(str(source.id), source.sheet_id)

    def page(self, *, after_id: str, limit: int) -> list[LocalLog]:
        """Newest first, keyset paged: the logs index. One sources read and
        one arrivals read for the page, never one per log; a log without a
        source is a broken invariant and raises."""
        sheets = self.sheets.page(kind=SheetKind.LOG, after_id=after_id, limit=limit)
        sheet_ids = [str(sheet.id) for sheet in sheets]
        sources = self._sources(sheet_ids)
        arrivals = self.sheets.last_arrivals(sheets)
        return [
            self._local(sheet, sources[str(sheet.id)], last_received_at=arrivals.get(str(sheet.id))) for sheet in sheets
        ]

    def create(self, *, owner_id: str, label: str, signal: str, listener_id: str, autofill: bool) -> LocalLog:
        """A hosted signal's log: its sheet, with the signal's base columns,
        and its hosted source."""
        shape = LOG_SHAPES[signal]
        with transaction.atomic():
            sheet = self.sheets.create(
                owner_id=owner_id,
                label=label,
                columns=list(shape.columns),
                origin=SheetOrigin.MANUAL,
                kind=SheetKind.LOG,
                autofill=autofill,
            )
            source = SignalSource(
                account_id=self.account_id,
                sheet_id=str(sheet.id),
                config=HostedSource(signal=signal, listener_id=listener_id),
            )
            source.save()
        return self._local(sheet, (source,))

    def create_custom(self, *, owner_id: str, label: str, columns: list[DeclaredColumn], autofill: bool) -> LocalLog:
        """A custom signal's log: a sheet with the declared columns (each
        value arrives with its row, so each is immutable) and its webhook
        source. Nothing to mint and no listener: rows arrive by push."""
        declared: list[PlainColumn] = []
        for column in columns:
            taken = {each.key for each in declared}
            key = new_column_key(column.label, taken=taken, count=len(declared))
            declared.append(PlainColumn(key=key, label=column.label, type=column.type, immutable=True))
        with transaction.atomic():
            sheet = self.sheets.create(
                owner_id=owner_id,
                label=label,
                columns=declared,
                origin=SheetOrigin.MANUAL,
                kind=SheetKind.LOG,
                autofill=autofill,
            )
            source = SignalSource(account_id=self.account_id, sheet_id=str(sheet.id), config=WebhookSource())
            source.save()
        return self._local(sheet, (source,))

    def edit(self, local_log: LocalLog, *, label: str | None) -> LocalLog:
        """A new label on the sheet (its autofill is the sheet's own, set
        through the sheet doors like any sheet's)."""
        sheet = local_log.sheet
        if label is not None:
            sheet = self.sheets.rename_log(sheet, label=label)
        return self._local(sheet, local_log.sources, last_received_at=local_log.last_received_at)

    def advance(self, source: SignalSource, *, to_cursor: str) -> bool:
        """Move the source's cursor past the items just appended, from where
        this read started: False when another read moved the cursor first
        (a page lands once), so the caller rolls back the rows it appended.
        Autofill needs no guard here: the append read it under the sheet's
        lock."""
        moved = SignalSource.objects.filter(id=source.id, account_id=self.account_id, cursor=source.cursor)
        return moved.update(cursor=to_cursor) == 1

    def wake(self) -> int:
        """Make every live polled source of the account due at once: what a
        reconnect does, since its logs were parked waiting for a working
        key."""
        polled = _live_polled()
        live = SignalSource.objects.filter(polled, account_id=self.account_id)
        return live.update(next_poll_at=timezone.now())

    def mark_gone(self, source: SignalSource) -> None:
        """The source stopped for good (a hosted listener the signals service
        no longer has): recorded once, the first time wins, and final, so it
        stops polling. Raises LogNotFound when the log itself is gone
        meanwhile (its delete removes the listener first, so a read in that
        window sees the 404 before the rows go)."""
        unmarked = SignalSource.objects.filter(id=source.id, account_id=self.account_id, gone_at__isnull=True)
        if unmarked.update(gone_at=timezone.now()) == 1:
            return
        still = SignalSource.objects.filter(id=source.id, account_id=self.account_id)
        if not still.exists():
            raise LogNotFound(source.sheet_id)

    def delete(self, local_log: LocalLog) -> None:
        """The sheet first, then its sources: the order a poller's append
        takes them (add_rows locks the sheet, then the cursor moves), so a
        delete and an append wait for each other rather than deadlock."""
        with transaction.atomic():
            self.sheets.delete_log(local_log.sheet)
            sources = SignalSource.objects.filter(account_id=self.account_id, sheet_id=local_log.sheet_id)
            sources.delete()

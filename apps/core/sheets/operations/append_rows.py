"""THE way rows enter a sheet, whoever brings them: a push, a person
adding rows, an import, a snapshot. The rows land through the sheet
service's primitive AND the workflow is told of the ones that hold
something, in ONE transaction, so a row holding a value never exists
with no work queued to fill it and no door can forget the second
half. The primitive (SheetService.add_rows) is called by nothing else
in production; a pin holds that.

Every door triggers, on purpose: a sheet that has no agent node yet
(a fresh import, a snapshot) is a no-op trigger, and a sheet that has
one is exactly the case where an arriving row must be judged.

A row that arrives BLANK (every cell empty: the sheet's add-rows
control makes such rows to be typed into) is created, counted and
ranked but not offered: nothing has arrived for an entry agent to
read, and a run on it would burn a model call to land a diagnosis and
count the row as attempted. It is offered when a person asks for a
column to fill (a fill whose prompt reads columns skips it as BLOCKED
until it holds something); an edit starts nothing, by the standing
rule.

Every caller names where its rows come from (`source`, a RowSource): a
LOG takes rows from its FEED only, and a standard sheet from anything but
a feed (SheetNotAppendable otherwise). `autofill=False` lands the rows
without the trigger (a log whose autofill is off): nothing is queued for
them, and a person fills them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction

from ..constants import IngestErrorCode, RowSource, SheetKind
from ..models import Sheet, SheetRow
from ..services.sheets import SheetService
from ..services.workflow_reactions import WorkflowReactions


class SheetNotAppendable(Exception):
    """An append refused by where its rows come from: a log takes rows
    from its feed only, and only a log takes a feed's rows."""

    code = IngestErrorCode.NOT_APPENDABLE

    def __init__(self, sheet_id: str, *, source: RowSource) -> None:
        if source == RowSource.FEED:
            detail = "only a log takes rows from a feed"
        else:
            detail = "a log's rows arrive from its feed; add rows to a sheet instead"
        super().__init__(detail)
        self.sheet_id = sheet_id
        self.source = source


def require_appendable(target_sheet: Sheet, *, source: RowSource) -> None:
    """The one rule for who may append: a log takes rows from its feed
    only, a standard sheet from anything else. The operation checks it,
    and a door whose append happens later (the ingest push) checks it
    first too."""
    from_feed = source == RowSource.FEED
    if (target_sheet.kind == SheetKind.LOG) != from_feed:
        raise SheetNotAppendable(str(target_sheet.id), source=source)


@dataclass
class AppendReport:
    """What one append did: the rows created (with ids, for a caller
    that keys on them) and the runs the trigger queued for them."""

    created: list[SheetRow]
    queued: int

    @property
    def added(self) -> int:
        return len(self.created)


class AppendRowsOperation:
    def __init__(
        self,
        *,
        account_id: str,
        target_sheet: Sheet,
        rows: Sequence[dict[str, str]],
        source: RowSource,
        autofill: bool = True,
    ) -> None:
        self.account_id = account_id
        self.target_sheet = target_sheet
        self.rows = list(rows)
        self.source = source
        self.autofill = autofill

    def run(self) -> AppendReport:
        """Raises SheetNotAppendable when the source does not fit the sheet
        (require_appendable), and the sheet service's SheetNotFound /
        SheetFull as they are: each door answers them in its own voice."""
        require_appendable(self.target_sheet, source=self.source)
        with transaction.atomic():
            created = SheetService(account_id=self.account_id).add_rows(self.target_sheet, self.rows)
            if not self.autofill:
                return AppendReport(created=created, queued=0)
            arrived = [row for row in created if _holds_something(row)]
            queued = WorkflowReactions(account_id=self.account_id).trigger(self.target_sheet, arrived)
        return AppendReport(created=created, queued=queued)


def _holds_something(row: SheetRow) -> bool:
    """A row with at least one non-blank cell: the one kind the
    workflow is told about."""
    return any(str(value or "").strip() for value in row.data.values())

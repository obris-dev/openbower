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

A LOG takes rows from its signal only: every other door is refused
(LogAppendRefused), and the signal's poller says so with `into_log`.
`react=False` lands the rows without the trigger (a log whose autofill
is off): they arrive blank of work, and a person fills them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction

from ..constants import IngestErrorCode, SheetKind
from ..models import Sheet, SheetRow
from ..services.sheets import SheetService
from ..services.workflow_reactions import WorkflowReactions


class LogAppendRefused(Exception):
    """An append to a log from a door other than its signal (a person, a
    push), refused: a log's rows are what its signal found."""

    code = IngestErrorCode.LOG_ROWS_FROM_SIGNAL

    def __init__(self, sheet_id: str) -> None:
        super().__init__("a log's rows arrive from its signal; add rows to a sheet instead")
        self.sheet_id = sheet_id


def require_appendable(target_sheet: Sheet, *, into_log: bool = False) -> None:
    """The one rule for who may append: any door to a standard sheet, only
    the signal (`into_log`) to a log. The operation checks it, and a door
    whose append happens later (the ingest push) checks it first too."""
    if target_sheet.kind == SheetKind.LOG and not into_log:
        raise LogAppendRefused(str(target_sheet.id))


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
        into_log: bool = False,
        react: bool = True,
    ) -> None:
        self.account_id = account_id
        self.target_sheet = target_sheet
        self.rows = list(rows)
        self.into_log = into_log
        self.react = react

    def run(self) -> AppendReport:
        """Raises LogAppendRefused for a log the caller is not the signal
        of, and the sheet service's SheetNotFound / SheetFull as they are:
        each door answers them in its own voice."""
        require_appendable(self.target_sheet, into_log=self.into_log)
        with transaction.atomic():
            created = SheetService(account_id=self.account_id).add_rows(self.target_sheet, self.rows)
            if not self.react:
                return AppendReport(created=created, queued=0)
            arrived = [row for row in created if _holds_something(row)]
            queued = WorkflowReactions(account_id=self.account_id).trigger(self.target_sheet, arrived)
        return AppendReport(created=created, queued=queued)


def _holds_something(row: SheetRow) -> bool:
    """A row with at least one non-blank cell: the one kind the
    workflow is told about."""
    return any(str(value or "").strip() for value in row.data.values())

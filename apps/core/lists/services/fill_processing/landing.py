"""Landing a run on its row: the ONE writer of a resolved row.

A row is resolved when three writes land together: the sheet value
(write-if-blank, through ListService), the cell truth (one
ListCellState per column the fill owns, carrying the run's tool
statuses), and the task's close with the run stored on it. They share
ONE transaction on purpose: a task whose lease was reclaimed mid-run
must produce NOTHING, never a value from one attempt wearing a
diagnosis from another, so a close that misses rolls the value write
back with it.

Two callers land rows, and before this module each restated the
writes: the consumer's terminal path, and its give-up past the
attempt cap (a run with no cells, its cause the last park's). What
differs between them is only HOW the task closes, so that is the one
thing a caller passes in. The counters DERIVE from the task rows and
cell states this writes, so nothing is folded back here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from django.db import transaction

from openbower_schema.fills import CellRunResult

from ...constants import StoredCellState
from ...models import Fill
from .. import cell_truth
from ..lists import ListService


class LandingContext(NamedTuple):
    """Who a run lands for, without a Fill: the identity a row's writes
    need. A fill-backed caller builds it from its Fill (`from_fill`); the
    automatic path (autofill) builds it from the task plus the agent's
    resolved column set, with `fill_run_id` NULL (the cell belongs to no
    run)."""

    account_id: str
    list_id: str
    column_keys: tuple[str, ...]
    fill_run_id: str | None
    config_fingerprint: str

    @classmethod
    def from_fill(cls, fill: Fill) -> LandingContext:
        return cls(
            account_id=fill.account_id,
            list_id=fill.list_id,
            column_keys=tuple(fill.column_keys),
            fill_run_id=str(fill.id),
            config_fingerprint=fill.config_fingerprint,
        )


class ClaimLost(Exception):
    """The task's close missed (the lease was reclaimed mid-run):
    raised inside the landing's transaction so everything staged
    beside it, the value write included, rolls back with it."""


class Landed(NamedTuple):
    """What landing decided per column: which columns hold a value
    now, and the cause every other column carries."""

    answered: frozenset[str]
    declined: StoredCellState


def _declined_cause(run: CellRunResult) -> StoredCellState:
    """WHY an output the run did not answer is empty: the run's own
    cause, or NO_EVIDENCE when it recorded none (a run stored before
    causes were, a give-up with nothing behind it)."""
    return StoredCellState(run.declined_cause or StoredCellState.NO_EVIDENCE)


def _unanswered(column_keys: tuple[str, ...], declined: StoredCellState) -> dict[str, StoredCellState]:
    """The starting state of every column the run owns: UNANSWERED,
    carrying the run's declined cause. The sheet write then moves the
    columns it filled to FILLED and the ones it refused to
    TYPE_MISMATCH; the rest keep the cause, which is what keeps them
    targetable by Continue."""
    return dict.fromkeys(column_keys, declined)


def land_row(
    ctx: LandingContext,
    row_id: str,
    run: CellRunResult,
    *,
    close: Callable[[dict], bool],
    lists: ListService | None = None,
) -> Landed | None:
    """Write what a run produced onto its row, in one transaction, and
    close the task through `close(result)` (True when the close
    landed). Returns None when the close missed: nothing was written.

    Per COLUMN, because a run answers outputs independently: a column
    the run answered settles FILLED (an OCCUPIED cell counts answered
    too: it holds a user's value that write-if-blank protected, and
    re-running it would only buy a skip; what the model said is in the
    stored run for a human to compare); a value the column's shape
    refused settles TYPE_MISMATCH (its own cause, the user's next step
    differs); every other column carries the run's declined cause and
    stays targetable instead of reading as answered. Raises the
    ListService's ListNotFound / RowNotFound as they are: a deleted
    sheet is the caller's story to resolve."""
    declined = _declined_cause(run)
    states = _unanswered(ctx.column_keys, declined)
    answered: set[str] = set()
    try:
        with transaction.atomic():
            if run.cells:
                keys = set(ctx.column_keys)
                mapped = {key: value for key, value in run.cells.items() if key in keys}
                writer = lists or ListService(account_id=ctx.account_id)
                written = writer.write_cells(ctx.list_id, row_id, mapped)
                answered = {*written.written, *written.occupied}
                for column_key in answered:
                    states[column_key] = StoredCellState.FILLED
                for mismatch in written.mismatched:
                    states[mismatch.key] = StoredCellState.TYPE_MISMATCH
            # Close BEFORE the ledger: the terminal order is ListRow,
            # NodeRun, ListCellState, the same order both delete
            # paths take (the reverse is an ABBA deadlock against a
            # mid-fill delete, and a blank landing holds no ListRow
            # lock to serialize on), and a reclaimed lease bows out
            # before any ledger write.
            if not close(run.model_dump()):
                raise ClaimLost()
            cell_truth.write(
                account_id=ctx.account_id,
                list_id=ctx.list_id,
                row_id=row_id,
                fill_run_id=ctx.fill_run_id,
                config_fingerprint=ctx.config_fingerprint,
                states=states,
                tools=run.tools,
            )
    except ClaimLost:
        return None
    return Landed(frozenset(answered), declined)

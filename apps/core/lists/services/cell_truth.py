"""The one writer of FillCellState: what a fill made of each cell.

A record exists for every cell a fill has RESOLVED, filled ones
included. There is nothing to write at admission (a queued FillTask on
a live fill is what makes a cell read pending) and nothing to sweep
when a fill stops (nothing was written for the rows it never reached).
Not writing a pending state is what makes a stop need no sweep: a
sweep would have to reconstruct each cell's previous truth from other
fills' records, which is a read-time replay this module exists to
have no part of.

So this module has exactly two jobs: write a row's states at the
terminal write, and purge a deleted list's.

Not account-scoped: the worker is a trusted process serving every
account's fills, and every caller has already resolved its parent
account-scoped. The `account_id` on the record is denormalized defence
in depth, not the guard.
"""

from __future__ import annotations

from ..constants import FILL_WRITE_BATCH, StoredCellState
from ..models import Fill, FillCellState

_UNIQUE_FIELDS = ["list_id", "row_id", "column_key"]
_UPSERT_FIELDS = ["state", "fill_id", "config_fingerprint", "updated_at"]


def write(fill: Fill, *, row_id: str, states: dict[str, StoredCellState]) -> None:
    """One row's cell states, written inside the terminal transaction
    that also writes the sheet row and closes the task.

    Per COLUMN, because a run answers outputs independently: a run that
    answered one output of three settles that column FILLED and leaves
    the other two carrying their own cause, so an unanswered column
    stays targetable instead of reading as answered.

    Every column the fill owns gets a record, including the answered
    ones. That is what keeps the per-column counts an indexed read
    rather than a scan of the sheet, and it is why absence means
    NEVER ATTEMPTED and nothing else."""
    if not states:
        return
    FillCellState.objects.bulk_create(
        [
            FillCellState(
                account_id=fill.account_id,
                list_id=fill.list_id,
                row_id=row_id,
                column_key=column_key,
                state=state,
                fill_id=str(fill.id),
                config_fingerprint=fill.config_fingerprint,
            )
            for column_key, state in states.items()
        ],
        update_conflicts=True,
        unique_fields=_UNIQUE_FIELDS,
        update_fields=_UPSERT_FIELDS,
    )


def uniform(fill: Fill, state: StoredCellState) -> dict[str, StoredCellState]:
    """The same state for every column the fill owns: the shape a run
    that answered NOTHING produces."""
    return dict.fromkeys(fill.column_keys, state)


def _purge_in_pages(**lookup: str) -> None:
    """Delete matching cell states a page at a time.

    The page is what bounds MEMORY, so the ids are read one page at a
    time rather than read in full and then sliced: reading them all
    first makes the peak the whole id set, which is the cost the
    paging exists to avoid. Each pass asks for the next
    FILL_WRITE_BATCH rows that still match, so the loop ends when a
    pass comes back empty."""
    while True:
        ids = list(FillCellState.objects.filter(**lookup).values_list("id", flat=True)[:FILL_WRITE_BATCH])
        if not ids:
            return
        FillCellState.objects.filter(id__in=ids).delete()


def purge_column(list_id: str, column_key: str) -> None:
    """A deleted column takes its cell states with it. Paged: the count
    is bounded by the cells the column answered, which has no ceiling
    short of the sheet."""
    _purge_in_pages(list_id=list_id, column_key=column_key)


def purge_list(list_id: str) -> None:
    """A deleted list takes its cell states with it. Paged for the same
    reason purge_column is."""
    _purge_in_pages(list_id=list_id)

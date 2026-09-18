"""The one writer of ListCellState: what a fill made of each cell.

A record exists for every cell a fill has RESOLVED, filled ones
included. There is nothing to write at admission (a queued NodeRun on
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

from openbower_kernel.batches import iter_id_pages

from ..constants import FILL_WRITE_BATCH, CellSource, StoredCellState
from ..models import Fill, ListCellState

_UNIQUE_FIELDS = ["list_id", "row_id", "column_key"]
# `source` rides the upsert: a fill landing over a hand-written cell
# re-attributes it, and a hand-written value over a fill's will too.
_UPSERT_FIELDS = ["state", "fill_run_id", "config_fingerprint", "tools", "source", "updated_at"]


def write(
    *,
    account_id: str,
    list_id: str,
    row_id: str,
    fill_run_id: str | None,
    config_fingerprint: str,
    states: dict[str, StoredCellState],
    tools: dict[str, str],
    source: CellSource = CellSource.FILL,
) -> None:
    """One row's cell states, written inside the terminal transaction
    that also writes the sheet row and closes the task.

    Takes the identity pieces, not a Fill: the fill-backed caller passes
    its Fill's, and the automatic path (autofill) passes the task's, with
    `fill_run_id` NULL (an autofilled cell belongs to no run).

    Per COLUMN, because a run answers outputs independently: a run that
    answered one output of three settles that column FILLED and leaves
    the other two carrying their own cause, so an unanswered column
    stays targetable instead of reading as answered.

    Every column the run owns gets a record, including the answered
    ones. That is what keeps the per-column counts an indexed read
    rather than a scan of the sheet, and it is why absence means
    NEVER ATTEMPTED and nothing else. `tools` is the run's per-tool
    provider statuses, the same on every column of the row: a filled cell
    keeps the record of a degraded tool beside its value. `source` says
    who wrote it: every caller today is a fill; the grid's edit path
    will write MANUAL once it exists, and completion reads both alike."""
    if not states:
        return
    ListCellState.objects.bulk_create(
        [
            ListCellState(
                account_id=account_id,
                list_id=list_id,
                row_id=row_id,
                column_key=column_key,
                state=state,
                fill_run_id=fill_run_id,
                config_fingerprint=config_fingerprint,
                tools=tools,
                source=source,
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
    """Delete matching cell states a page at a time (the page bounds
    memory; a deleted page never comes back)."""
    for ids in iter_id_pages(ListCellState.objects.filter(**lookup), batch=FILL_WRITE_BATCH):
        ListCellState.objects.filter(id__in=ids).delete()


def purge_column(list_id: str, column_key: str) -> None:
    """A deleted column takes its cell states with it. Paged: the count
    is bounded by the cells the column answered, which has no ceiling
    short of the sheet."""
    _purge_in_pages(list_id=list_id, column_key=column_key)


def purge_list(list_id: str) -> None:
    """A deleted list takes its cell states with it. Paged for the same
    reason purge_column is."""
    _purge_in_pages(list_id=list_id)

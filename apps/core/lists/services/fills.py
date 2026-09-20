"""User-facing fill control and reads, thin and account-scoped
(cross-tenant access reads as not-found, like every lists service).
A fill is a job of kind `fill`; this service reads and stops those
jobs. Authorization is ACCOUNT MEMBERSHIP: a fill on a shared list is
a shared fill, so any teammate may cancel; `user_id` on the row is
attribution only."""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from django.db import models

from agents.constants import ToolStatus
from jobs.models import Job
from openbower_schema.fills import ColumnFillSummary, FillCounters, FillError
from openbower_schema.lists import AiColumn, CellStateWire

from ..constants import NON_TERMINAL_NODE_RUN_STATES, NodeRunStatus, StoredCellState
from ..models import List, ListCellState, ListRow, NodeRun
from . import fill_progress
from .cell_states import CellStateService

# The wire's one non-terminal state. A STRING here and not a StoredCellState
# member on purpose: the server never stores it, it derives it from the
# queue, so it has no place in the stored taxonomy.
PENDING = "pending"


class FillProgress(NamedTuple):
    """What a fill's runs say about it at read time: the counters, the
    liveness stamp, and whether any run has been claimed (the wire's
    pending | running line)."""

    counters: FillCounters
    heartbeat: datetime | None
    started: bool


def derive_counters(fill_run_id: str) -> FillCounters:
    """The wire's progress, DERIVED at read time from the task rows and
    cell states rather than a stored counter: attempted is the run's
    settled (DONE) tasks; filled is the rows this run wrote a FILLED
    cell for (distinct, since a multi-column row is one filled row);
    blank is the settled remainder; transient is the rows currently
    parked in retry (a non-terminal task carrying the park mark).

    Two indexed reads, no sheet scan: DONE task count on the reclaim
    index, FILLED cell count on the cell-state index."""
    attempted = NodeRun.objects.filter(fill_run_id=fill_run_id, status=NodeRunStatus.DONE).count()
    filled = (
        ListCellState.objects.filter(fill_run_id=fill_run_id, state=StoredCellState.FILLED)
        .values("row_id")
        .distinct()
        .count()
    )
    transient = NodeRun.objects.filter(
        fill_run_id=fill_run_id, status__in=NON_TERMINAL_NODE_RUN_STATES, parked=True
    ).count()
    return FillCounters(attempted=attempted, filled=filled, blank=attempted - filled, transient=transient)


def derive_heartbeat(fill_run_id: str) -> datetime | None:
    """The run's liveness stamp, DERIVED as the latest state change
    across its tasks (the reclaim scan's own cursor): a run whose tasks keep
    moving reads fresh, one that has gone silent reads stale. None when
    the run has no task carrying one yet."""
    return NodeRun.objects.filter(fill_run_id=fill_run_id).aggregate(latest=models.Max("last_state_change_at"))[
        "latest"
    ]


def page_progress(fill_run_ids: list[str]) -> dict[str, FillProgress]:
    """The counters, heartbeat and started flag for a PAGE of runs in a
    fixed number of grouped reads, so the fills poll does not pay
    derive_counters + derive_heartbeat PER run (a 3+4N walk on a
    four-second poll). Four aggregates GROUPED by fill_run_id: DONE
    tasks, distinct FILLED rows, parked non-terminal tasks, and the
    latest state change with the highest attempt count. An id with no
    matching rows reads all-zero / None / not started. The ids are
    already account-scoped by the caller (page_for_list), and
    fill_run_id is unique, so no extra tenant filter is needed here."""
    if not fill_run_ids:
        return {}
    done = {
        r["fill_run_id"]: r["n"]
        for r in NodeRun.objects.filter(fill_run_id__in=fill_run_ids, status=NodeRunStatus.DONE)
        .values("fill_run_id")
        .annotate(n=models.Count("id"))
    }
    filled = {
        r["fill_run_id"]: r["n"]
        for r in ListCellState.objects.filter(fill_run_id__in=fill_run_ids, state=StoredCellState.FILLED)
        .values("fill_run_id")
        .annotate(n=models.Count("row_id", distinct=True))
    }
    parked = {
        r["fill_run_id"]: r["n"]
        for r in NodeRun.objects.filter(
            fill_run_id__in=fill_run_ids, status__in=NON_TERMINAL_NODE_RUN_STATES, parked=True
        )
        .values("fill_run_id")
        .annotate(n=models.Count("id"))
    }
    moved = {
        r["fill_run_id"]: (r["latest"], r["attempts"])
        for r in NodeRun.objects.filter(fill_run_id__in=fill_run_ids)
        .values("fill_run_id")
        .annotate(latest=models.Max("last_state_change_at"), attempts=models.Max("attempts"))
    }
    out: dict[str, FillProgress] = {}
    for fid in fill_run_ids:
        attempted = done.get(fid, 0)
        filled_rows = filled.get(fid, 0)
        latest, attempts = moved.get(fid, (None, 0))
        out[fid] = FillProgress(
            counters=FillCounters(
                attempted=attempted, filled=filled_rows, blank=attempted - filled_rows, transient=parked.get(fid, 0)
            ),
            heartbeat=latest,
            started=bool(attempts),
        )
    return out


class FillNotFound(Exception):
    """Missing OR foreign fill (cross-tenant reads as not-found)."""


class FillService:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id
        self.cell_states = CellStateService(account_id=account_id)

    def get(self, fill_run_id: str) -> Job:
        fill = fill_progress.fill_jobs().filter(id=fill_run_id, account_id=self.account_id).first()
        if fill is None:
            raise FillNotFound(fill_run_id)
        return fill

    def page_for_list(self, list_id: str, *, after_id: str, limit: int) -> list[Job]:
        """Keyset by -id, OPEN runs only: a terminal run's story (its
        status, its error) lands on the column summary the moment it
        leaves this list, so the poll carries in-flight work alone. A
        failed run is still first-class there, never a 4xx."""
        qs = fill_progress.open_fills().filter(account_id=self.account_id, subject_id=list_id).order_by("-id")
        if after_id:
            qs = qs.filter(id__lt=after_id)
        return list(qs[:limit])

    def cancel(self, fill_run_id: str) -> Job:
        """The stop from outside: the queued runs are abandoned, the
        job flips; the worker's per-row liveness check sees the flip
        between rows (in-flight spend is sunk cost). A fill already
        terminal cancels to a no-op, not an error: the user's intent
        (this fill must not spend further) already holds."""
        # Account-scoped FIRST (a foreign id reads as not found), then
        # the SHARED transition, so the user's cancel and the worker's
        # cannot order their writes differently.
        self.get(fill_run_id)
        fill_progress.cancel(fill_run_id)
        return self.get(fill_run_id)

    def cell_states_for_rows(self, target_list: List, rows: list[ListRow]) -> dict[str, dict[str, CellStateWire]]:
        """row id -> {column key: CellStateWire} for one page of rows.

        Two sources, and neither is a stored "pending":

        DIAGNOSED BLANKS come from ListCellState, one indexed query,
        each with the tool statuses of the run that wrote it. FILLED
        travels ONLY when that run had a degraded tool (the value is
        the renderer's already; the mark beside it is not): a clean
        filled cell is the absence of an entry. Never-attempted is
        the absence of a record.

        PENDING is DERIVED: a cell is pending when a queued task on an
        open fill covers its column. The fill's column set was frozen
        at consent and the queue still holds it, so this is exact
        without anything having been written to the sheet at admission
        and without anything needing to be swept when a fill stops.

        Pending is applied SECOND on purpose: a cell an earlier fill
        diagnosed and an open fill has re-queued is being worked on
        now, and that is what the user should see.
        """
        ai_keys = {column.key for column in target_list.columns if isinstance(column, AiColumn)}
        if not ai_keys or not rows:
            return {}
        row_ids = [str(r.id) for r in rows]
        states: dict[str, dict[str, CellStateWire]] = {}
        recorded = self.cell_states.iter_recorded(str(target_list.id), row_ids=row_ids, column_keys=ai_keys)
        for row_id, column_key, state, tools in recorded:
            tools = tools or {}
            degraded = any(status != ToolStatus.OPEN for status in tools.values())
            if state == StoredCellState.FILLED and not degraded:
                continue
            states.setdefault(row_id, {})[column_key] = CellStateWire(state=state, tools=tools)
        live = {
            str(fill_run_id): [key for key in (payload.get("column_keys") or ()) if key in ai_keys]
            for fill_run_id, payload in fill_progress.open_fills()
            .filter(account_id=self.account_id, subject_id=str(target_list.id))
            .values_list("id", "payload")
        }
        if not live:
            return states
        queued = NodeRun.objects.filter(
            account_id=self.account_id,
            fill_run_id__in=list(live),
            row_id__in=row_ids,
            status__in=NON_TERMINAL_NODE_RUN_STATES,
        ).values_list("fill_run_id", "row_id")
        for fill_run_id, row_id in queued:
            for column_key in live[fill_run_id]:
                states.setdefault(row_id, {})[column_key] = CellStateWire(state=PENDING)
        return states

    def column_summaries(self, target_list: List) -> list[ColumnFillSummary]:
        """Per-column coverage AND the terminal story, for the fills
        poll: how many cells the column has FILLED, how many it has
        RESOLVED, which fill speaks for it, that fill's status, and
        its error when it failed (the page ships open runs only, so
        the summary is where a terminal story lands).

        THREE bounded reads, no sheet scan anywhere. The totals come off
        one grouped read of the cell states (both numbers on the same
        index, bounded by the cells a fill has actually touched rather
        than by the size of the sheet, which is the whole reason
        FILLED is stored instead of counted out of the row JSON); the
        story off one projected read of the jobs the columns name, and
        one grouped read of their runs for the pending | running line.

        `current_fill_id` is READ off the column, where admission wrote
        it, never reconstructed by walking the sheet's fills; a column
        whose run has not opened reads as the contract's empty string.

        `attempted` is the honest denominator for `filled`: a targeted
        cell resolves to exactly one state, so their sum is what the
        column was asked to do. Pairing `filled` with the SHEET's row
        count answers a different question and makes a scoped fill read
        as a failure."""
        fill_columns = [column for column in target_list.columns if isinstance(column, AiColumn)]
        if not fill_columns:
            return []
        keys = [column.key for column in fill_columns]
        filled: dict[str, int] = {}
        attempted: dict[str, int] = {}
        rows = self.cell_states.iter_counts_by_column(str(target_list.id), column_keys=keys)
        for key, state, count in rows:
            attempted[key] = attempted.get(key, 0) + count
            if state == StoredCellState.FILLED:
                filled[key] = filled.get(key, 0) + count
        current_by_key = {column.key: column.current_fill_id for column in fill_columns}
        current_ids = [fill_run_id for fill_run_id in current_by_key.values() if fill_run_id]
        started = {
            r["fill_run_id"]: bool(r["attempts"])
            for r in NodeRun.objects.filter(fill_run_id__in=current_ids)
            .values("fill_run_id")
            .annotate(attempts=models.Max("attempts"))
        }
        runs_by_id = {
            str(fill_run_id): (
                fill_progress.word_of(status, started=started.get(str(fill_run_id), False)),
                code,
                message,
            )
            for fill_run_id, status, code, message in fill_progress.fill_jobs()
            .filter(account_id=self.account_id, subject_id=str(target_list.id), id__in=current_ids)
            .values_list("id", "status", "error_code", "error")
        }
        summaries: list[ColumnFillSummary] = []
        for column in fill_columns:
            current_id = current_by_key[column.key]
            status, code, message = runs_by_id.get(current_id, ("", "", ""))
            # Both legs travel together (tier 1), gated on the
            # DOCUMENTED predicate (the run FAILED), never on the
            # coupling that error_code happens to be blank on every
            # other path. Reading only the run the column currently
            # names is what lets a newer clean run clear an old
            # failure without anything being swept.
            error = FillError(code=code, message=message) if status == "failed" and code else None
            summaries.append(
                ColumnFillSummary(
                    column_key=column.key,
                    current_fill_id=current_id,
                    current_status=status,
                    last_error=error,
                    filled=filled.get(column.key, 0),
                    attempted=attempted.get(column.key, 0),
                )
            )
        return summaries

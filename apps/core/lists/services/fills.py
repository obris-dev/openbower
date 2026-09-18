"""User-facing fill control and reads, thin and account-scoped
(cross-tenant access reads as not-found, like every lists service).
Authorization is ACCOUNT MEMBERSHIP: a fill on a shared list is a
shared fill, so any teammate may cancel; `user_id` on the row is
attribution only."""

from __future__ import annotations

from datetime import datetime

from django.db import models

from agents.constants import ToolStatus
from openbower_schema.fills import CellRunResult, ColumnFillSummary, FillCounters, FillError
from openbower_schema.lists import AiColumn, CellStateWire

from ..constants import (
    LIVE_FILL_STATUSES,
    NON_TERMINAL_NODE_RUN_STATES,
    FillKind,
    FillStatus,
    NodeRunStatus,
    StoredCellState,
)
from ..models import Fill, List, ListCellState, ListRow, NodeRun
from .cell_states import CellStateService
from .fill_progress import stop_fill

# The wire's one non-terminal state. A STRING here and not a StoredCellState
# member on purpose: the server never stores it, it derives it from the
# queue, so it has no place in the stored taxonomy.
PENDING = "pending"


def derive_counters(fill: Fill) -> FillCounters:
    """The wire's progress, DERIVED at read time from the task rows and
    cell states rather than a stored counter: attempted is the run's
    settled (DONE) tasks; filled is the rows this run wrote a FILLED
    cell for (distinct, since a multi-column row is one filled row);
    blank is the settled remainder; transient is the rows currently
    parked in retry (a non-terminal task carrying the park mark).

    Two indexed reads, no sheet scan: DONE task count on the reclaim
    index, FILLED cell count on the cell-state index."""
    fill_run_id = str(fill.id)
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


def derive_heartbeat(fill: Fill) -> datetime | None:
    """The run's liveness stamp, DERIVED as the latest state change
    across its tasks (the reclaim scan's own cursor): a run whose tasks keep
    moving reads fresh, one that has gone silent reads stale. None when
    the run has no task carrying one yet."""
    return NodeRun.objects.filter(fill_run_id=str(fill.id)).aggregate(latest=models.Max("last_state_change_at"))[
        "latest"
    ]


def page_progress(fill_run_ids: list[str]) -> dict[str, tuple[FillCounters, datetime | None]]:
    """The counters + heartbeat for a PAGE of runs in a fixed number of
    grouped reads, so the fills poll does not pay derive_counters +
    derive_heartbeat PER run (a 3+4N walk on a four-second poll). Four
    aggregates GROUPED by fill_run_id: DONE tasks, distinct FILLED rows,
    parked non-terminal tasks, and the latest state change. An id with no
    matching rows reads all-zero / None. The ids are already
    account-scoped by the caller (page_for_list), and fill_run_id is
    unique, so no extra tenant filter is needed here."""
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
    heartbeats = {
        r["fill_run_id"]: r["latest"]
        for r in NodeRun.objects.filter(fill_run_id__in=fill_run_ids)
        .values("fill_run_id")
        .annotate(latest=models.Max("last_state_change_at"))
    }
    out: dict[str, tuple[FillCounters, datetime | None]] = {}
    for fid in fill_run_ids:
        attempted = done.get(fid, 0)
        filled_rows = filled.get(fid, 0)
        out[fid] = (
            FillCounters(
                attempted=attempted, filled=filled_rows, blank=attempted - filled_rows, transient=parked.get(fid, 0)
            ),
            heartbeats.get(fid),
        )
    return out


class FillNotFound(Exception):
    """Missing OR foreign fill (cross-tenant reads as not-found)."""


class FillService:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id
        self.cell_states = CellStateService(account_id=account_id)

    def get(self, fill_run_id: str) -> Fill:
        try:
            return Fill.objects.get(id=fill_run_id, account_id=self.account_id)
        except Fill.DoesNotExist as e:
            raise FillNotFound(fill_run_id) from e

    def test_result(self, fill: Fill) -> CellRunResult | None:
        """A test run's stored result: its one task's record, read
        back through the contract model it was written through. Gated
        on the TASK (DONE with a stored record), never on the fill's
        status: the landing commits the task first and flips the fill
        after, so a cancel racing that gap leaves a CANCELLED fill
        holding a fully paid result, and a status gate would strand
        it. None while the task is unfinished, and always for
        kind=normal (a normal fill's results live on the sheet)."""
        if fill.kind != FillKind.TEST:
            return None
        task = NodeRun.objects.filter(fill_run_id=str(fill.id)).order_by("position").first()
        if task is None or task.status != NodeRunStatus.DONE or not task.result:
            return None
        return CellRunResult.model_validate(task.result)

    def page_for_list(self, list_id: str, *, after_id: str, limit: int) -> list[Fill]:
        """Keyset by -id, LIVE runs only: a terminal run's story (its
        status, its error) lands on the column summary the moment it
        leaves this list, so the poll carries in-flight work alone. A
        failed run is still first-class there, never a 4xx."""
        # The snapshot is deferred, not shipped OR loaded: the wire
        # dropped it and nothing on the poll path reads it, so the
        # page must not pay the JSONB either (a test captures the
        # endpoint's SQL and refuses any query touching the column).
        qs = (
            Fill.objects.filter(
                account_id=self.account_id,
                list_id=list_id,
                status__in=LIVE_FILL_STATUSES,
            )
            .defer("config_snapshot")
            .order_by("-id")
        )
        if after_id:
            qs = qs.filter(id__lt=after_id)
        return list(qs[:limit])

    def cancel(self, fill_run_id: str) -> Fill:
        """CAS from live states; the worker's per-row liveness check
        sees the flip between rows (in-flight spend is sunk cost). A
        fill already terminal cancels to a no-op, not an error: the
        user's intent (this fill must not spend further) already
        holds."""
        # Account-scoped FIRST (a foreign id reads as not found), then
        # the SHARED transition, so the user's cancel and the worker's
        # cannot order their writes differently.
        self.get(fill_run_id)
        stop_fill(fill_run_id, FillStatus.CANCELLED)
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

        PENDING is DERIVED: a cell is pending when a queued task on a
        live fill covers its column. The fill's target set was frozen
        at consent and the queue still holds it, so this is exact
        without anything having been written to the sheet at admission
        and without anything needing to be swept when a fill stops.

        Pending is applied SECOND on purpose: a cell an earlier fill
        diagnosed and a live fill has re-queued is being worked on now,
        and that is what the user should see.
        """
        fill_keys = {column.key for column in target_list.columns if isinstance(column, AiColumn)}
        if not fill_keys or not rows:
            return {}
        row_ids = [str(r.id) for r in rows]
        states: dict[str, dict[str, CellStateWire]] = {}
        recorded = self.cell_states.iter_recorded(str(target_list.id), row_ids=row_ids, column_keys=fill_keys)
        for row_id, column_key, state, tools in recorded:
            tools = tools or {}
            degraded = any(status != ToolStatus.OPEN for status in tools.values())
            if state == StoredCellState.FILLED and not degraded:
                continue
            states.setdefault(row_id, {})[column_key] = CellStateWire(state=state, tools=tools)
        live = {
            str(fill_run_id): [key for key in (keys or ()) if key in fill_keys]
            for fill_run_id, keys in Fill.objects.filter(
                account_id=self.account_id,
                list_id=str(target_list.id),
                status__in=LIVE_FILL_STATUSES,
            ).values_list("id", "column_keys")
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
        its error when it failed (the page ships live runs only, so
        the summary is where a terminal story lands).

        TWO bounded reads, no sheet scan anywhere. The totals come off
        one grouped read of the cell states (both numbers on the same
        index, bounded by the cells a fill has actually touched rather
        than by the size of the sheet, which is the whole reason
        FILLED is stored instead of counted out of the row JSON); the
        story off one projected read of the runs the columns name.

        `current_fill_id` is READ off the column, where admission wrote
        it. It used to be reconstructed by walking every fill the sheet
        had ever had; a column that predates the write reads as the
        contract's documented empty string (this service supplies it),
        so nothing needs backfilling.

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
        # The newest run's status and error, off the runs the columns
        # name: one query keyed by those ids, bounded by the sheet's
        # fill columns, and PROJECTED to the three facts it feeds (the
        # snapshot must not ride the poll through a side channel either).
        current_by_key = {column.key: column.current_fill_id for column in fill_columns}
        runs_by_id = {
            str(fill_run_id): (status, code, message)
            for fill_run_id, status, code, message in Fill.objects.filter(
                account_id=self.account_id,
                list_id=str(target_list.id),
                id__in=[fill_run_id for fill_run_id in current_by_key.values() if fill_run_id],
            ).values_list("id", "status", "error_code", "error_message")
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
            error = FillError(code=code, message=message) if status == FillStatus.FAILED and code else None
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

"""User-facing fill control and reads, thin and account-scoped
(cross-tenant access reads as not-found, like every lists service).
Authorization is ACCOUNT MEMBERSHIP: a fill on a shared list is a
shared fill, so any teammate may cancel; `user_id` on the row is
attribution only."""

from __future__ import annotations

from django.db import models

from agents.constants import ToolStatus
from openbower_schema.fills import ColumnFillSummary, FillError
from openbower_schema.lists import CellStateWire

from ..constants import LIVE_FILL_STATUSES, FillStatus, FillTaskStatus, StoredCellState
from ..models import Fill, FillCellState, FillTask, List, ListRow
from .fill_progress import stop_fill

# The wire's one non-terminal state. A STRING here and not a StoredCellState
# member on purpose: the server never stores it, it derives it from the
# queue, so it has no place in the stored taxonomy.
PENDING = "pending"


class FillNotFound(Exception):
    """Missing OR foreign fill (cross-tenant reads as not-found)."""


class FillService:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def get(self, fill_id: str) -> Fill:
        try:
            return Fill.objects.get(id=fill_id, account_id=self.account_id)
        except Fill.DoesNotExist as e:
            raise FillNotFound(fill_id) from e

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
            Fill.objects.filter(account_id=self.account_id, list_id=list_id, status__in=LIVE_FILL_STATUSES)
            .defer("config_snapshot")
            .order_by("-id")
        )
        if after_id:
            qs = qs.filter(id__lt=after_id)
        return list(qs[:limit])

    def cancel(self, fill_id: str) -> Fill:
        """CAS from live states; the worker's per-row liveness check
        sees the flip between rows (in-flight spend is sunk cost). A
        fill already terminal cancels to a no-op, not an error: the
        user's intent (this fill must not spend further) already
        holds."""
        # Account-scoped FIRST (a foreign id reads as not found), then
        # the SHARED transition, so the user's cancel and the worker's
        # cannot order their writes differently.
        self.get(fill_id)
        stop_fill(fill_id, FillStatus.CANCELLED)
        return self.get(fill_id)

    def cell_states_for_rows(self, target_list: List, rows: list[ListRow]) -> dict[str, dict[str, CellStateWire]]:
        """row id -> {column key: CellStateWire} for one page of rows.

        Two sources, and neither is a stored "pending":

        DIAGNOSED BLANKS come from FillCellState, one indexed query,
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
        fill_keys = {column["key"] for column in target_list.columns if column.get("fill")}
        if not fill_keys or not rows:
            return {}
        row_ids = [str(r.id) for r in rows]
        states: dict[str, dict[str, CellStateWire]] = {}
        recorded = (
            FillCellState.objects.filter(
                account_id=self.account_id,
                list_id=str(target_list.id),
                row_id__in=row_ids,
                column_key__in=fill_keys,
            )
            # DB-side narrowing only (a pre-tools filled row); the
            # Python guard below stays the rule, because a clean run
            # records {"web_search": "open"}, never {}.
            .exclude(state=StoredCellState.FILLED, tools={})
            .values_list("row_id", "column_key", "state", "tools")
        )
        for row_id, column_key, state, tools in recorded:
            tools = tools or {}
            degraded = any(status != ToolStatus.OPEN for status in tools.values())
            if state == StoredCellState.FILLED and not degraded:
                continue
            states.setdefault(row_id, {})[column_key] = CellStateWire(state=state, tools=tools)
        live = {
            str(fill_id): [key for key in (keys or ()) if key in fill_keys]
            for fill_id, keys in Fill.objects.filter(
                account_id=self.account_id, list_id=str(target_list.id), status__in=LIVE_FILL_STATUSES
            ).values_list("id", "column_keys")
        }
        if not live:
            return states
        queued = FillTask.objects.filter(
            account_id=self.account_id,
            fill_id__in=list(live),
            row_id__in=row_ids,
            status=FillTaskStatus.QUEUED,
        ).values_list("fill_id", "row_id")
        for fill_id, row_id in queued:
            for column_key in live[fill_id]:
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
        fill_columns = [column for column in target_list.columns if column.get("fill")]
        if not fill_columns:
            return []
        keys = [column["key"] for column in fill_columns]
        filled: dict[str, int] = {}
        attempted: dict[str, int] = {}
        rows = (
            FillCellState.objects.filter(account_id=self.account_id, list_id=str(target_list.id), column_key__in=keys)
            .values_list("column_key", "state")
            .annotate(n=models.Count("id"))
        )
        for key, state, count in rows:
            attempted[key] = attempted.get(key, 0) + count
            if state == StoredCellState.FILLED:
                filled[key] = filled.get(key, 0) + count
        # The newest run's status and error, off the runs the columns
        # name: one query keyed by those ids, bounded by the sheet's
        # fill columns, and PROJECTED to the three facts it feeds (the
        # snapshot must not ride the poll through a back door either).
        current_by_key = {
            column["key"]: (column.get("fill") or {}).get("current_fill_id", "") for column in fill_columns
        }
        runs_by_id = {
            str(fill_id): (status, code, message)
            for fill_id, status, code, message in Fill.objects.filter(
                account_id=self.account_id,
                list_id=str(target_list.id),
                id__in=[fill_id for fill_id in current_by_key.values() if fill_id],
            ).values_list("id", "status", "error_code", "error_message")
        }
        summaries: list[ColumnFillSummary] = []
        for column in fill_columns:
            current_id = current_by_key[column["key"]]
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
                    column_key=column["key"],
                    current_fill_id=current_id,
                    current_status=status,
                    last_error=error,
                    filled=filled.get(column["key"], 0),
                    attempted=attempted.get(column["key"], 0),
                )
            )
        return summaries

"""User-facing fill control and reads, thin and account-scoped
(cross-tenant access reads as not-found, like every lists service).
Authorization is ACCOUNT MEMBERSHIP: a fill on a shared list is a
shared fill, so any teammate may cancel; `user_id` on the row is
attribution only."""

from __future__ import annotations

from django.db import models

from agents.constants import ToolStatus
from openbower_schema.fills import ColumnFillSummary
from openbower_schema.lists import CellStateWire

from ..constants import LIVE_FILL_STATUSES, FillStatus, FillTaskStatus, StoredCellState
from ..models import Fill, FillCellState, FillTask, List, ListRow
from .fill_queue import stop_fill

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
        """Keyset by -id, ALL states visible: a failed fill is a
        first-class API object with its error, not a 4xx."""
        qs = Fill.objects.filter(account_id=self.account_id, list_id=list_id).order_by("-id")
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

    def cell_states_for_rows(self, target: List, rows: list[ListRow]) -> dict[str, dict[str, CellStateWire]]:
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
        fill_keys = {column["key"] for column in target.columns if column.get("fill")}
        if not fill_keys or not rows:
            return {}
        row_ids = [str(r.id) for r in rows]
        states: dict[str, dict[str, CellStateWire]] = {}
        recorded = FillCellState.objects.filter(
            account_id=self.account_id,
            list_id=str(target.id),
            row_id__in=row_ids,
            column_key__in=fill_keys,
        ).values_list("row_id", "column_key", "state", "tools")
        for row_id, column_key, state, tools in recorded:
            tools = tools or {}
            degraded = any(status != ToolStatus.OPEN for status in tools.values())
            if state == StoredCellState.FILLED and not degraded:
                continue
            states.setdefault(row_id, {})[column_key] = CellStateWire(state=state, tools=tools)
        live = {
            str(fill_id): [key for key in (keys or ()) if key in fill_keys]
            for fill_id, keys in Fill.objects.filter(
                account_id=self.account_id, list_id=str(target.id), status__in=LIVE_FILL_STATUSES
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

    def column_summaries(self, target: List) -> list[ColumnFillSummary]:
        """Per-column coverage for the fills poll: how many cells the
        column has FILLED, how many it has RESOLVED, and which fill
        speaks for it.

        ONE grouped read of the cell states, no sheet scan anywhere.
        Both numbers come off the same index, and the read is bounded
        by the cells a fill has actually touched rather than by the
        size of the sheet, which is the whole reason FILLED is stored
        instead of counted out of the row JSON.

        `current_fill_id` is READ off the column, where admission wrote
        it. It used to be reconstructed by walking every fill the sheet
        had ever had; a column that predates the write reads the
        contract's own documented default, an empty string, so nothing
        needs backfilling.

        `attempted` is the honest denominator for `filled`: a targeted
        cell resolves to exactly one state, so their sum is what the
        column was asked to do. Pairing `filled` with the SHEET's row
        count answers a different question and makes a scoped fill read
        as a failure."""
        fill_columns = [column for column in target.columns if column.get("fill")]
        if not fill_columns:
            return []
        keys = [column["key"] for column in fill_columns]
        filled: dict[str, int] = {}
        attempted: dict[str, int] = {}
        rows = (
            FillCellState.objects.filter(account_id=self.account_id, list_id=str(target.id), column_key__in=keys)
            .values_list("column_key", "state")
            .annotate(n=models.Count("id"))
        )
        for key, state, count in rows:
            attempted[key] = attempted.get(key, 0) + count
            if state == StoredCellState.FILLED:
                filled[key] = filled.get(key, 0) + count
        return [
            ColumnFillSummary(
                column_key=column["key"],
                current_fill_id=(column.get("fill") or {}).get("current_fill_id", ""),
                filled=filled.get(column["key"], 0),
                attempted=attempted.get(column["key"], 0),
            )
            for column in fill_columns
        ]

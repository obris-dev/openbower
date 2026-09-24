"""User-facing fill control and reads, thin and account-scoped
(cross-tenant access reads as not-found, like every lists service).
A fill is a job of kind `fill`; this service reads and stops those
jobs. Authorization is ACCOUNT MEMBERSHIP: a fill on a shared list is
a shared fill, so any teammate may cancel; `user_id` on the row is
attribution only."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from django.db import models

from agents.constants import ToolStatus
from jobs.constants import OPEN_JOB_STATES
from jobs.models import Job
from openbower_schema.fills import ColumnFillSummary, FillCounters
from openbower_schema.lists import AiColumn, CellStateWire, WebhookColumn

from ..constants import NON_TERMINAL_NODE_RUN_STATES, NodeRunStatus, StoredCellState
from ..models import List, ListCellState, ListRow, NodeRun
from . import fill_progress
from .cell_states import CellStateService
from .node_runs import NodeRunFlow
from .workflows import columns_by_node

# The wire's one non-terminal state. A STRING here and not a StoredCellState
# member on purpose: the server never stores it, it derives it from the
# queue, so it has no place in the stored taxonomy.
PENDING = "pending"


class FillReadout(NamedTuple):
    """What a fill's runs say about it at read time: the counters, the
    liveness stamp, and whether any run has been claimed (the wire's
    pending | running line)."""

    counters: FillCounters
    heartbeat: datetime
    started: bool


def page_progress(fills: Sequence[Job]) -> dict[str, FillReadout]:
    """The counters, heartbeat and started flag for the fills given, in
    a fixed number of grouped reads whatever their count (the echo
    paths pass one, the poll a page): the ONE reader of what a fill's
    runs say about it. Four aggregates GROUPED by fill_run_id: DONE
    tasks, distinct FILLED rows, parked non-terminal tasks, and the
    latest state change; the started flag is the flow's one question,
    asked of the open fills only. The heartbeat
    is the later of that latest change and the job's own stamp, which
    every transition writes (a walk still queuing, a park between
    polls, a stop), so a fill that has done nothing yet still reads as
    alive from its enqueue. A fill with no runs reads all-zero and not
    started. The jobs are already account-scoped by the caller, and
    fill_run_id is unique, so no extra tenant filter is needed here."""
    if not fills:
        return {}
    fill_run_ids = [str(fill.id) for fill in fills]
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
    latest = {
        r["fill_run_id"]: r["latest"]
        for r in NodeRun.objects.filter(fill_run_id__in=fill_run_ids)
        .values("fill_run_id")
        .annotate(latest=models.Max("last_state_change_at"))
    }
    started = NodeRunFlow.started_fills([str(fill.id) for fill in fills if fill.status in OPEN_JOB_STATES])
    out: dict[str, FillReadout] = {}
    for fill in fills:
        fid = str(fill.id)
        attempted = done.get(fid, 0)
        filled_rows = filled.get(fid, 0)
        moved = latest.get(fid)
        heartbeat = max(moved, fill.last_state_change_at) if moved else fill.last_state_change_at
        out[fid] = FillReadout(
            counters=FillCounters(
                attempted=attempted, filled=filled_rows, blank=attempted - filled_rows, transient=parked.get(fid, 0)
            ),
            heartbeat=heartbeat,
            started=fid in started,
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
        qs = fill_progress.open_fills().filter(account_id=self.account_id, target_id=list_id).order_by("-id")
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
        """row id -> {column key: CellStateWire} for one page of rows,
        every node column's (AI and Send webhook alike), off ONE ledger.

        Two sources, and neither is a stored "pending":

        RECORDED states come from ListCellState, one indexed query,
        each with the tool statuses of the run that wrote it. FILLED
        travels ONLY when that run had a degraded tool (the value is
        the renderer's already; the mark beside it is not): a clean
        filled cell is the absence of an entry. Never-attempted is
        the absence of a record.

        PENDING is DERIVED, one rule for every node column: an open run
        of the node that fills the cell, for its row, WHICHEVER lane
        queued it (a fill's, a pushed row's autofill, a cleared
        barrier's, a send owed or being retried). One open run per
        (row, node) is what the run table holds, so this reads what is
        actually being worked on; a rule that looked at open fills
        alone left a row being autofilled looking untouched. Nothing is
        written to the sheet at admission and nothing needs sweeping at
        a stop.

        Pending is applied SECOND on purpose: a cell an earlier run
        diagnosed and an open one has re-queued is being worked on
        now, and that is what the user should see.
        """
        ai_keys = {column.key for column in target_list.columns if isinstance(column, AiColumn)}
        webhook_key_by_node = {
            column.node_id: column.key for column in target_list.columns if isinstance(column, WebhookColumn)
        }
        keys = ai_keys | set(webhook_key_by_node.values())
        if not keys or not rows:
            return {}
        row_ids = [str(r.id) for r in rows]
        states: dict[str, dict[str, CellStateWire]] = {}
        recorded = self.cell_states.iter_recorded(str(target_list.id), row_ids=row_ids, column_keys=keys)
        for row_id, column_key, state, tools in recorded:
            tools = tools or {}
            degraded = any(status != ToolStatus.OPEN for status in tools.values())
            if state == StoredCellState.FILLED and not degraded:
                continue
            states.setdefault(row_id, {})[column_key] = CellStateWire(state=state, tools=tools)
        # ONE read for every node column: the node that fills each cell,
        # whatever its kind, and its open runs over this page's rows.
        keys_by_node: dict[str, list[str]] = {
            node_id: [key for key in node_keys if key in ai_keys]
            for node_id, node_keys in columns_by_node(target_list).items()
        }
        for node_id, key in webhook_key_by_node.items():
            keys_by_node.setdefault(node_id, []).append(key)
        open_runs = NodeRun.objects.filter(
            account_id=self.account_id,
            node_id__in=list(keys_by_node),
            row_id__in=row_ids,
            status__in=NON_TERMINAL_NODE_RUN_STATES,
        ).values_list("row_id", "node_id")
        for row_id, node_id in open_runs:
            for column_key in keys_by_node[node_id]:
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
        one read of the OPEN ones' runs for the pending | running line
        (a settled fill's runs are never read again).

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
        # The jobs FIRST, so the one question about runs is asked of the
        # open fills only: a settled fill's runs are never read here.
        jobs = list(
            fill_progress.fill_jobs()
            .filter(account_id=self.account_id, target_id=str(target_list.id), id__in=current_ids)
            .values_list("id", "status", "error_code", "error")
        )
        open_ids = [str(fill_run_id) for fill_run_id, status, _code, _message in jobs if status in OPEN_JOB_STATES]
        started = NodeRunFlow.started_fills(open_ids)
        runs_by_id = {
            str(fill_run_id): (
                fill_progress.word_of(status, started=str(fill_run_id) in started),
                fill_progress.error_of(status, code, message),
            )
            for fill_run_id, status, code, message in jobs
        }
        summaries: list[ColumnFillSummary] = []
        for column in fill_columns:
            current_id = current_by_key[column.key]
            status, error = runs_by_id.get(current_id, ("", None))
            # The why travels with the word under the one rule
            # (fill_progress.error_of). Reading only the run the column
            # currently names is what lets a newer clean run clear an
            # old failure without anything being swept.
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

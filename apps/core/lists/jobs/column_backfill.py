"""The column backfill: for one node, over the whole sheet, queue a run for
every row the node's processor says is owed one, when a structural
change (a webhook column added, or its wait set changed) makes rows
owed work that no landing will ever offer. The per-kind judgement is
the processor's, so the walker knows no kind and no column. A fill's walk
is the fill job's own first slices (lists/jobs/fill.py); this job
carries no consent and no range.

Walking a sheet is not a request's job (a 50k-row sheet paged inside
the request would hold every other writer behind it), and it is not a
NodeRun (a run is one node on one row; this MAKES runs), so it is a
job: one page per slice in sheet order, the cursor the last row walked
(by id, with the rank it had, so a re-spacing mid-walk is harmless and
a row moved out from under the walk is the next walk's, like a row
appended after). No lock:
the columns array is never written here, and the one guarantee that
matters, a row offered once however many walkers and landings offer
it, is the open-run key on the processor's insert, so a reclaimed slice
re-walked is a no-op."""

from __future__ import annotations

from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel

from jobs.kinds.base import JobKind
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import FILL_SCAN_CHUNK
from ..processors import FillMode, FillScope, processor_for
from ..services.lists import ListNotFound, ListService, RowCursor
from ..services.workflows import NodeNotFound, WorkflowService


class ColumnBackfillProgress(BaseModel):
    # The last row walked (its id, and the rank it had); the next slice
    # pages after it in sheet order.
    after_id: str = ""
    after_rank: str = ""


class ColumnBackfillJob(JobKind[ColumnBackfillProgress]):
    KIND: ClassVar[str] = "column_backfill"
    Progress = ColumnBackfillProgress
    list_id: str
    node_id: str

    def run(self, job: Job, progress: ColumnBackfillProgress) -> ColumnBackfillProgress | None:
        """One page of rows after the cursor, offered to the node's
        processor as the sheet stands NOW. Done when the sheet is walked
        or the sheet or the node is gone."""
        lists = ListService(account_id=job.account_id)
        try:
            target_list = lists.get(self.list_id)
        except ListNotFound:
            return None
        try:
            node = WorkflowService(account_id=job.account_id).get_node(self.node_id)
        except NodeNotFound:
            return None
        after = RowCursor(progress.after_id, progress.after_rank) if progress.after_id else None
        page = lists.rows_page(target_list, after=after, limit=FILL_SCAN_CHUNK)
        if not page:
            return None
        processor = processor_for(account_id=job.account_id, node=node)
        scope = FillScope(mode=FillMode.BACKFILL)
        now = timezone.now()
        processor.enqueue_runs(target_list, page, scope=scope, now=now)
        return ColumnBackfillProgress(after_id=str(page[-1].id), after_rank=page[-1].rank)


register(ColumnBackfillJob)

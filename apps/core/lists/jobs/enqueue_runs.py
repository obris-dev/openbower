"""The structural walker: for one node, over the whole sheet, queue a
run for every row the node's processor says is owed one (a webhook
column added or its wait set changed). The per-kind judgement is the
processor's, so the walker knows no kind and no column. A fill's walk
is the fill job's own first slices (lists/jobs/fill.py); this job
carries no consent and no range.

Walking a sheet is not a request's job (a 50k-row sheet paged inside
the request would hold every other writer behind it), and it is not a
NodeRun (a run is one node on one row; this MAKES runs), so it is a
job: one page per slice, the cursor the last position walked. No lock:
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
from ..processors import WalkScope, processor_for
from ..services.lists import ListNotFound, ListService
from ..services.workflows import NodeNotFound, WorkflowService


class EnqueueRuns(JobKind):
    KIND: ClassVar[str] = "enqueue_runs"
    list_id: str
    node_id: str

    class Progress(BaseModel):
        # The last sheet position walked; the next slice pages after it.
        after_position: int = 0

    def run(self, job: Job, progress: Progress) -> Progress | None:
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
        page = lists.rows_page(target_list, after_position=progress.after_position, limit=FILL_SCAN_CHUNK)
        if not page:
            return None
        processor = processor_for(account_id=job.account_id, node=node, scope=WalkScope())
        processor.enqueue_runs(target_list, page, now=timezone.now())
        return self.Progress(after_position=page[-1].position)


register(EnqueueRuns)

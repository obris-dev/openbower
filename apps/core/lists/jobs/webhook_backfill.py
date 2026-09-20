"""The webhook backfill: when a Send webhook column is added over a
sheet with history, or its wait set changes, every row already
complete for the wait set is owed a run. Walking the sheet is not the
request's job (a 50k-row sheet under the list lock for the whole walk
would stall every other writer), and it is not a NodeRun (a run is one
node on one row; this MAKES runs), so it is a job: one page per slice,
the cursor the last position walked, the judgement the node kind's
PROCESSOR (the walker knows no kind and no column). No lock: the
columns array is never written here, and the one guarantee that
matters, a row offered once however many walkers and landings offer
it, is the open-run key on the processor's insert. Its output is
DEFERRED webhook runs, which the flush picks up exactly as it picks up
the runs a landing wrote; the flush cannot tell them apart, which is
the point."""

from __future__ import annotations

from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel

from jobs.kinds.base import JobKind
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import FILL_SCAN_CHUNK
from ..processors import processor_for
from ..services.lists import ListNotFound, ListService
from ..services.workflows import NodeNotFound, WorkflowService


class WebhookBackfill(JobKind):
    KIND: ClassVar[str] = "webhook_backfill"
    list_id: str
    node_id: str

    class Progress(BaseModel):
        # The last sheet position walked; the next slice pages after it.
        after_position: int = 0

    def run(self, job: Job, progress: Progress) -> Progress | None:
        """One page of rows after the cursor, offered to the node's
        processor as the sheet stands NOW (a wait set edited mid-walk
        applies to the rest of the walk). Done when the page is empty,
        or when the column or its sheet is gone. A column deleted
        between this read and the insert leaves a few runs for a gone
        node, which the flush closes as failed; the ledger ends the
        same."""
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
        processor_for(account_id=job.account_id, node=node).enqueue_runs(target_list, page, now=timezone.now())
        return self.Progress(after_position=page[-1].position)


register(WebhookBackfill)

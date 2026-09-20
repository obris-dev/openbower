"""The webhook backfill: when a Send webhook column is added over a
sheet with history, or its wait set changes, every row already
complete for the wait set is owed a run. Walking the sheet is not the
request's job (a 50k-row sheet under the list lock for the whole walk
would stall every other writer), and it is not a NodeRun (a run is one
node on one row; this MAKES runs), so it is a job: one page per slice,
the cursor the last position walked, the judgement the node kind's
PROCESSOR (the walker knows no kind). No lock: the columns array is
never written here, and the one guarantee that matters, a row offered
once however many walkers and landings offer it, is the open-run key
on the insert. Its output is DEFERRED webhook runs, which the flush
picks up exactly as it picks up the runs a landing wrote; the flush
cannot tell them apart, which is the point."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel

from jobs.kinds.base import JobKind
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import FILL_SCAN_CHUNK, FILL_WRITE_BATCH
from ..models import NodeRun
from ..processors import processor_for
from ..processors.base import CellRecords
from ..services.cell_states import CellStateService
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
        or when the column, its sheet, or its inputs are gone (nothing
        left to backfill). A column deleted between this read and the
        insert leaves a few runs for a gone node, which the flush closes
        as failed; the ledger ends the same."""
        lists = ListService(account_id=job.account_id)
        try:
            target_list = lists.get(self.list_id)
        except ListNotFound:
            return None
        try:
            node = WorkflowService(account_id=job.account_id).get_node(self.node_id)
        except NodeNotFound:
            return None
        processor = processor_for(account_id=job.account_id, node=node)
        keys = processor.needs(target_list)
        if not keys:
            return None
        page = lists.rows_page(target_list, after_position=progress.after_position, limit=FILL_SCAN_CHUNK)
        if not page:
            return None
        list_id = str(target_list.id)
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=job.account_id)
        row_ids = [str(row.id) for row in page]
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=row_ids, column_keys=keys
        ):
            records[row_id][column_key] = (state, updated_at)
        by_row: dict[str, CellRecords] = records
        runs = processor.materialize(target_list, page, by_row, now=timezone.now())
        if runs:
            NodeRun.objects.bulk_create(runs, ignore_conflicts=True, batch_size=FILL_WRITE_BATCH)
        return self.Progress(after_position=page[-1].position)


register(WebhookBackfill)

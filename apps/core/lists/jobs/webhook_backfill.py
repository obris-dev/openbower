"""The webhook backfill: when a Send webhook column is added over a
sheet with history, or its wait set changes, every row already
complete for the wait set is owed a run. Walking the sheet is not the
request's job (a 50k-row sheet under the list lock for the whole walk
would stall every other writer), and it is not a NodeRun (a run is one
node on one row; this MAKES runs), so it is a job: one page per slice,
the cursor the last position walked. No lock: the columns array is
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
from ..nodes.wait_until import WaitUntil
from ..services.cell_states import CellStateService
from ..services.digest_payload import completion_of
from ..services.lists import ListNotFound, ListService
from ..services.webhook_runs import runs_for_complete_row, wait_keys_of
from ..services.workflows import NodeNotFound, WorkflowService, config_as


class WebhookBackfill(JobKind):
    KIND: ClassVar[str] = "webhook_backfill"
    list_id: str
    node_id: str

    class Progress(BaseModel):
        # The last sheet position walked; the next slice pages after it.
        after_position: int = 0

    def run(self, job: Job, progress: Progress) -> Progress | None:
        """One page of rows after the cursor: the rows complete for the
        wait set as it stands NOW (a wait set edited mid-walk applies to
        the rest of the walk) gain a run at the next window. Done when
        the page is empty, or when the column, its sheet, or its wait
        set is gone (nothing left to backfill). A column deleted between
        this read and the insert leaves a few runs for a gone node,
        which the flush closes as failed; the ledger ends the same."""
        lists = ListService(account_id=job.account_id)
        try:
            target_list = lists.get(self.list_id)
        except ListNotFound:
            return None
        workflows = WorkflowService(account_id=job.account_id)
        try:
            webhook_node = workflows.get_node(self.node_id)
        except NodeNotFound:
            return None
        path_nodes = workflows.nodes_on_path(webhook_node.path_id)
        if not path_nodes or path_nodes[0].kind != WaitUntil.KIND:
            return None
        wait = config_as(path_nodes[0], WaitUntil)
        wait_keys = wait_keys_of(account_id=job.account_id, target_list=target_list, wait=wait)
        if not wait_keys:
            return None
        page = lists.rows_page(target_list, after_position=progress.after_position, limit=FILL_SCAN_CHUNK)
        if not page:
            return None
        list_id = str(target_list.id)
        records: dict[str, dict[str, tuple[str, datetime]]] = defaultdict(dict)
        cell_states = CellStateService(account_id=job.account_id)
        row_ids = [str(row.id) for row in page]
        for row_id, column_key, state, updated_at in cell_states.iter_records(
            list_id, row_ids=row_ids, column_keys=wait_keys
        ):
            records[row_id][column_key] = (state, updated_at)
        now = timezone.now()
        runs: list[NodeRun] = []
        for row in page:
            if completion_of(records[str(row.id)], wait_keys) is None:
                continue
            runs.extend(
                runs_for_complete_row(
                    account_id=job.account_id, list_id=list_id, row=row, webhook_nodes=[webhook_node], now=now
                )
            )
        if runs:
            NodeRun.objects.bulk_create(runs, ignore_conflicts=True, batch_size=FILL_WRITE_BATCH)
        return self.Progress(after_position=page[-1].position)


register(WebhookBackfill)

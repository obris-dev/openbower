"""The one walker: for one node, over a scope of rows, queue a run for
every row the node's processor says is owed one. A fresh fill, a
refill, and a webhook backfill are all this job with a different scope;
the per-kind judgement is the processor's, so the walker knows no kind
and no column.

Walking a sheet is not a request's job (a 50k-row sheet paged inside
the request would hold every other writer behind it), and it is not a
NodeRun (a run is one node on one row; this MAKES runs), so it is a
job: one page per slice, the cursor the last position walked. No lock:
the columns array is never written here, and the one guarantee that
matters, a row offered once however many walkers and landings offer
it, is the open-run key on the processor's insert, so a reclaimed slice
re-walked is a no-op.

A FILL scope is a consent, so the walk has a range (`until_position`:
rows at or below the count the user echoed; a row appended after the
click sits above it and is never walked) and may have a limit (a scoped
fill's first N qualifying rows). When the range is walked the fill's
target set is whole: its denominator settles to the runs actually
queued, `targeted_at` is stamped, and the completion rule runs once (a
fill whose rows all settled before the walk ended completes here). A
fill cancelled mid-walk stops the walk, and a slice that landed runs
after the cancel's sweep abandons them itself, so nothing is left READY
that nothing will ever run."""

from __future__ import annotations

from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel, Field

from jobs.kinds.base import JobKind
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import FILL_SCAN_CHUNK
from ..processors import WalkScope, processor_for
from ..services import fill_progress
from ..services.lists import ListNotFound, ListService
from ..services.workflows import NodeNotFound, WorkflowService


class EnqueueRuns(JobKind):
    KIND: ClassVar[str] = "enqueue_runs"
    list_id: str
    node_id: str
    scope: WalkScope = Field(default_factory=WalkScope)

    class Progress(BaseModel):
        # The last sheet position walked; the next slice pages after it.
        after_position: int = 0
        # Runs queued so far, for a scoped fill's limit.
        offered: int = 0

    def run(self, job: Job, progress: Progress) -> Progress | None:
        """One page of rows after the cursor, within the scope's range,
        offered to the node's processor as the sheet stands NOW. Done
        when the range is walked (a fill settles its targets), when the
        sheet, the node, or the fill is gone, or when a scoped fill has
        its N."""
        lists = ListService(account_id=job.account_id)
        try:
            target_list = lists.get(self.list_id)
        except ListNotFound:
            return None
        try:
            node = WorkflowService(account_id=job.account_id).get_node(self.node_id)
        except NodeNotFound:
            return None
        fill_run_id = self.scope.fill_run_id
        if fill_run_id and not fill_progress.is_live(fill_run_id):
            return None
        page = lists.rows_page(
            target_list,
            after_position=progress.after_position,
            limit=FILL_SCAN_CHUNK,
            until_position=self.scope.until_position,
        )
        if not page:
            return self._finish(fill_run_id)
        processor = processor_for(account_id=job.account_id, node=node, scope=self.scope)
        offered = progress.offered
        now = timezone.now()
        if self.scope.limit:
            # A scoped fill wants its first N qualifying rows: the page
            # is offered in windows no wider than what is still owed, so
            # the processor can never queue past N.
            start = 0
            while start < len(page) and offered < self.scope.limit:
                window = page[start : start + (self.scope.limit - offered)]
                offered += processor.enqueue_runs(target_list, window, now=now)
                start += len(window)
        else:
            offered += processor.enqueue_runs(target_list, page, now=now)
        if fill_run_id and not fill_progress.is_live(fill_run_id):
            # Cancelled while this slice was inserting: the cancel's
            # sweep ran before these runs existed, so sweep them now.
            fill_progress.abandon_queued(fill_run_id)
            return None
        if self.scope.limit and offered >= self.scope.limit:
            return self._finish(fill_run_id)
        return self.Progress(after_position=page[-1].position, offered=offered)

    @staticmethod
    def _finish(fill_run_id: str) -> None:
        if fill_run_id:
            fill_progress.settle_targets(fill_run_id)
        return None


register(EnqueueRuns)

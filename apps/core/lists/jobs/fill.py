"""A fill IS a job: the consent (the payload), the walk that queues its
runs (the first slices), and the wait for those runs to settle (the
rest), one lifecycle on the one job row. `fill_run_id` on a run and on
a cell is this job's id.

The walk: one page of rows per slice within the consent's range
(`until_position`: rows at or below the count the user echoed; a row
appended after the click sits above it and is never walked), offered
to the agent kind's processor, which judges each row by the walk's
mode and inserts under the open-run key so a re-walked slice is a
no-op. A scoped fill (`limit`) offers windows no wider than what is
still owed. When the range is walked (or the limit met) the target set
is WHOLE: the denominator settles to the runs actually queued (a count
off the ledger, never an accumulator) and `targeted_at` is stamped.

The wait: once targeted, each slice asks whether any run is still
open and, if so, raises JobWaiting for FILL_POLL_SECONDS (parked, no
attempt spent, the cursor untouched); when none is, the job is done
and the fill reads complete.
The consumer never writes this job's status: a config-tier failure
stops the job from outside through the shared stop, and the runner's
own park and settle miss on a job stopped meanwhile.

No list lock anywhere: the columns array is never written here, and
the one guarantee that matters (a row offered once however many
walkers offer it) is the open-run key on the processor's insert. A
fill stopped mid-walk stops the walk, and a slice that landed runs
after the stop's sweep abandons them itself, so nothing is left READY
that nothing will ever run. A fill whose list is gone exits: the
list's delete purges the job, and a job the delete missed finds no
list and ends."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel

from jobs.kinds.base import JobKind, JobWaiting
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import FILL_POLL_SECONDS, FILL_SCAN_CHUNK
from ..processors import WalkMode, WalkScope, processor_for
from ..services import fill_progress
from ..services.lists import ListNotFound, ListService
from ..services.node_runs import NodeRunFlow
from ..services.workflows import NodeNotFound, WorkflowService


class FillProgress(BaseModel):
    # The last sheet position walked; the next slice pages after it.
    after_position: int = 0
    # Runs queued so far, for a scoped fill's limit.
    offered: int = 0
    # When the target set became whole, and the denominator it settled
    # to. Null while the walk is still queuing.
    targeted_at: datetime | None = None
    targeted: int = 0


class FillJob(JobKind[FillProgress]):
    KIND: ClassVar[str] = fill_progress.FILL_KIND
    Progress = FillProgress
    list_id: str
    node_id: str
    agent_id: str
    # The columns this fill owns, frozen at consent (each output's own
    # key is its column key).
    column_keys: list[str]
    mode: WalkMode
    # A REMAINING walk: the stopped fill it resumes (its ABANDONED runs
    # bound the offer; "" = the column's whole remainder), and the
    # columns it judges owed-ness across.
    owed_by: str = ""
    judged_keys: list[str] = []
    # The consent range and the scoped fill's first N (0 = no bound).
    until_position: int = 0
    limit: int = 0
    # The denominator at birth: the count the user was shown.
    consented: int

    def scope(self, job: Job) -> WalkScope:
        return WalkScope(
            mode=self.mode,
            fill_run_id=str(job.id),
            owed_by=self.owed_by,
            column_keys=self.judged_keys,
            until_position=self.until_position,
            limit=self.limit,
        )

    def run(self, job: Job, progress: FillProgress) -> FillProgress | None:
        if not fill_progress.is_open(str(job.id)):
            # Stopped from outside since the claim: this slice is the
            # last, and the runner's settle misses on purpose.
            return None
        if progress.targeted_at is None:
            return self._walk(job, progress)
        if NodeRunFlow.has_open_for_fill(str(job.id)):
            raise JobWaiting(FILL_POLL_SECONDS)
        return None

    def _walk(self, job: Job, progress: FillProgress) -> FillProgress | None:
        lists = ListService(account_id=job.account_id)
        try:
            target_list = lists.get(self.list_id)
        except ListNotFound:
            return None
        try:
            node = WorkflowService(account_id=job.account_id).get_node(self.node_id)
        except NodeNotFound:
            return None
        page = lists.rows_page(
            target_list,
            after_position=progress.after_position,
            limit=FILL_SCAN_CHUNK,
            until_position=self.until_position,
        )
        if not page:
            return self._targeted(job, progress)
        processor = processor_for(account_id=job.account_id, node=node, scope=self.scope(job))
        offered = progress.offered
        now = timezone.now()
        if self.limit:
            # A scoped fill wants its first N qualifying rows: the page
            # is offered in windows no wider than what is still owed, so
            # the processor can never queue past N.
            start = 0
            while start < len(page) and offered < self.limit:
                window = page[start : start + (self.limit - offered)]
                offered += processor.enqueue_runs(target_list, window, now=now)
                start += len(window)
        else:
            offered += processor.enqueue_runs(target_list, page, now=now)
        if not fill_progress.is_open(str(job.id)):
            # Stopped while this slice was inserting: the stop's sweep
            # ran before these runs existed, so sweep them now.
            NodeRunFlow.abandon_queued_for_fill(str(job.id))
            return None
        if self.limit and offered >= self.limit:
            return self._targeted(job, FillProgress(after_position=page[-1].position, offered=offered))
        return FillProgress(after_position=page[-1].position, offered=offered)

    def _targeted(self, job: Job, progress: FillProgress) -> FillProgress:
        """The target set is whole: the denominator settles to the runs
        queued and the stamp lands. The runner loops straight into the
        wait, so a fill whose rows all settled before the walk ended,
        or that targeted nothing, completes on the same tick."""
        return FillProgress(
            after_position=progress.after_position,
            offered=progress.offered,
            targeted_at=timezone.now(),
            targeted=NodeRunFlow.count_for_fill(str(job.id)),
        )

    def on_stop(self, job: Job) -> None:
        """A stop from outside sweeps the queue FIRST (NodeRun before
        Job, the terminal write path's own order), then the flip."""
        NodeRunFlow.abandon_queued_for_fill(str(job.id))


register(FillJob)

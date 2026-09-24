"""A fill IS a job: the consent (the payload), the walk that queues its
runs (the first slices), and the wait for those runs to settle (the
rest), one lifecycle on the one job row. `fill_run_id` on a run and on
a cell is this job's id.

The walk: one page of rows per slice, in SHEET ORDER (the order the
user sees the fill march down), over the consent set (`until_id`: the
rows that existed at the click; a row appended after is newer and is
never walked) and at most `covered` rows of it, offered
to the agent kind's processor, which judges each row by the walk's
mode and inserts under the fill's row key so a re-walked slice is a
no-op (and under the open-run key, so a row another lane holds is
left to it). A scoped fill (`max_row_count`) tells the processor what is still owed and it stops there, judging no further than
what is still owed. When the range is walked (or the scope met) the
target set
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
walkers offer it) is the processor's insert: the fill's row key within
this fill, the open-run key across lanes. The
cursor names the last row walked (its id, and the rank it had), and
the one thing that rewrites ranks, a re-space, waits for the list's
open fills, so that rank is the truth between two slices; a row moved
from below the cursor to above it during the seconds a walk takes is
not walked and waits for the next refill, exactly like a row appended
after the click, and one moved the other way is offered twice and
dropped by the fill's row key. A fill stopped mid-walk stops the walk,
and a slice that lands runs after the stop's sweep leaves them for
the reclaim's orphan judgement, which abandons them by fill id. A fill whose list is gone exits: the
list's delete purges the job, and a job the delete missed finds no
list and ends."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from django.utils import timezone
from pydantic import BaseModel

from agents.services import AgentNotFound
from jobs.kinds.base import JobFailed, JobKind, JobWaiting
from jobs.kinds.registry import register
from jobs.models import Job

from ..constants import AGENT_MISSING_MESSAGE, FILL_POLL_SECONDS, FILL_SCAN_CHUNK, FillFailureCode
from ..processors import FillMode, FillScope, processor_for
from ..services import fill_progress
from ..services.lists import ListNotFound, ListService, RowCursor
from ..services.node_runs import NodeRunFlow
from ..services.workflows import NodeNotFound, WorkflowService


class FillProgress(BaseModel):
    # The last row walked (its id, and the rank it had); the next slice
    # pages after it in sheet order.
    after_id: str = ""
    after_rank: str = ""
    # Rows walked so far, for the consent's count; rows offered so far,
    # for a scoped fill's max_row_count. A row another lane's open run
    # holds is offered but gets no run of this fill: it is being filled
    # all the same, so it spends the scope.
    walked: int = 0
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
    mode: FillMode
    # A REMAINING walk: the stopped fill it resumes (its ABANDONED runs
    # bound the offer; "" = the column's whole remainder), and the
    # columns it judges across.
    resumed_fill_id: str = ""
    judged_keys: list[str] = []
    # The consent SET (the newest row id at the click; "" = no bound),
    # how many of its rows the walk may cover in sheet order (the count
    # the user was shown; the whole sheet for a scoped fill), and the
    # scoped fill's first N qualifying rows (0 = no bound).
    until_id: str = ""
    covered: int
    max_row_count: int = 0

    @property
    def consented(self) -> int:
        """The denominator at birth: the rows the user was shown, or a
        scoped fill's N when that is smaller. Derived, so the two
        numbers cannot disagree."""
        return min(self.max_row_count, self.covered) if self.max_row_count else self.covered

    def scope(self, job: Job) -> FillScope:
        return FillScope(
            mode=self.mode,
            fill_run_id=str(job.id),
            resumed_fill_id=self.resumed_fill_id,
            column_keys=self.judged_keys,
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
        # The covered count bounds the rows walked, in sheet order: the
        # last page asks for only what the consent still allows.
        remaining = self.covered - progress.walked
        if remaining <= 0:
            return self._targeted(job, progress)
        after = RowCursor(progress.after_id, progress.after_rank) if progress.after_id else None
        page = lists.rows_page(target_list, after=after, limit=min(FILL_SCAN_CHUNK, remaining), until_id=self.until_id)
        if not page:
            return self._targeted(job, progress)
        processor = processor_for(account_id=job.account_id, node=node, scope=self.scope(job))
        offered = progress.offered
        now = timezone.now()
        try:
            # A scoped fill wants its first N qualifying rows: the
            # processor is told what is still owed and stops there,
            # judging no further; unscoped, the whole page is offered.
            remaining = self.max_row_count - offered if self.max_row_count else 0
            offered += processor.enqueue_runs(target_list, page, now=now, limit=remaining)
        except AgentNotFound:
            # The judgement reads the agent live and it is gone: the
            # fill's own verdict, not a crash to retry. The runner runs
            # on_stop before it fails the job, so what earlier slices
            # queued is swept and nothing is left READY.
            raise JobFailed(FillFailureCode.AGENT_MISSING, AGENT_MISSING_MESSAGE) from None
        advanced = FillProgress(
            after_id=str(page[-1].id),
            after_rank=page[-1].rank,
            walked=progress.walked + len(page),
            offered=offered,
        )
        if self.max_row_count and offered >= self.max_row_count:
            return self._targeted(job, advanced)
        return advanced

    def _targeted(self, job: Job, progress: FillProgress) -> FillProgress:
        """The target set is whole: the denominator settles to the runs
        queued and the stamp lands. The runner loops straight into the
        wait, so a fill whose rows all settled before the walk ended,
        or that targeted nothing, completes on the same tick."""
        return FillProgress(
            after_id=progress.after_id,
            after_rank=progress.after_rank,
            walked=progress.walked,
            offered=progress.offered,
            targeted_at=timezone.now(),
            targeted=NodeRunFlow.count_for_fill(str(job.id)),
        )

    def on_stop(self, job: Job) -> None:
        """A stop sweeps the queue at once, the fast path. A slice that
        inserts into a fill closing under it, or a stop that dies before
        this runs, leaves runs READY that nothing publishes; the
        reclaim's abandon_orphans judgement is what makes the rule hold
        regardless, so no ordering here is load-bearing."""
        NodeRunFlow.abandon_queued_for_fill(str(job.id))


register(FillJob)

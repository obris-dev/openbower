"""A fill's lifecycle, over its JOB row: a fill is a job of kind
`fill` (lists/jobs/fill.py), and this module is every read and write
of that row the lists app makes outside the kind's own slices. The
lifecycle IS the job's: open while READY or PROCESSING (the walk, then
the poll for its runs), DONE when every run settled, FAILED when a
worker found the config cannot run, CANCELLED when a user stopped it.
The wire's five words (the contract's FillStatusWire) derive from that
plus the runs (`status_of`).

Progress counters are NOT written here: the wire derives them from the
task rows and cell states at read time (services.fills.derive_counters).

Split from the task state machine on purpose: node_runs.py is TASK
lifecycle (claim, settle, park), this is the fill's. Plain functions,
because none of this holds state: a fill id in, one UPDATE out. Not
account-scoped: the consumer is a trusted process serving every
account's fills, and the user-facing service resolves its fill
account-scoped before it calls cancel.
"""

from __future__ import annotations

from collections.abc import Iterator

from django.db.models import QuerySet

from jobs.constants import OPEN_JOB_STATES, JobStatus
from jobs.models import Job
from jobs.services import JobService
from openbower_schema.fills import FillStatusWire

from .node_runs import NodeRunFlow

# The fill kind's name, restated here rather than imported from the
# kind module: the kind imports this module for its lifecycle, and the
# name is the one fact the two share.
FILL_KIND = "fill"


def fill_jobs() -> QuerySet[Job]:
    """Every fill, any account, any status."""
    return Job.objects.filter(kind=FILL_KIND)


def open_fills() -> QuerySet[Job]:
    """Every fill still owed work: walking, or polling its runs."""
    return fill_jobs().filter(status__in=OPEN_JOB_STATES)


def iter_open_fills() -> Iterator[Job]:
    """Every open fill, oldest first, LAZILY (single-pass). The manual
    provisioner iterates these and publishes each fill's READY tasks, so
    per-fill depth is the fairness point (a wide fill cannot flood the
    bus). Streamed via .iterator() so a growing number of open fills
    never materializes as one list."""
    yield from open_fills().order_by("id").iterator()


def open_fill_count(account_id: str) -> int:
    """The account's fills still open (a bench run is not a fill and
    never counts): the cap's count."""
    return open_fills().filter(account_id=account_id).count()


def is_open(fill_run_id: str) -> bool:
    return open_fills().filter(id=fill_run_id).exists()


def cancel(fill_run_id: str) -> bool:
    """The user's Stop, and the worker's list-gone resolution (a user
    deletion reads as CANCELLED, never failed: failed is config-tier
    and carries an error the UI dresses as a failure story). The
    queued runs are abandoned first (the kind's `on_stop`), then the
    job flips; a fill already terminal stays put."""
    return JobService.Global.cancel(fill_run_id)


def fail(fill_run_id: str, *, code: str, message: str) -> bool:
    """The breaker path (config-tier: a dead or throttling provider
    fails the whole fill loudly). From open states only; a cancel that
    already landed stays cancelled."""
    return JobService.Global.fail(fill_run_id, code=code, message=message)


def started(fill_run_id: str) -> bool:
    """Whether any of the fill's runs has been claimed: the line between
    the wire's `pending` and `running`."""
    return NodeRunFlow.any_claimed_for_fill(fill_run_id)


def status_of(job: Job, *, started: bool) -> FillStatusWire:
    """The wire's word for a fill job, derived and never stored (the
    contract's FillStatusWire is the one definition of the vocabulary):
    its terminal status as it is, an open one as `running` once a run
    has been claimed, else `pending`."""
    return word_of(job.status, started=started)


def word_of(status: str, *, started: bool) -> FillStatusWire:
    """The same derivation off a projected job status (the column
    summaries read columns, not rows)."""
    if status == JobStatus.DONE:
        return "complete"
    if status == JobStatus.FAILED:
        return "failed"
    if status == JobStatus.CANCELLED:
        return "cancelled"
    return "running" if started else "pending"

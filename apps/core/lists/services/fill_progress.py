"""A fill's lifecycle, over its JOB row: a fill is a job of kind
`fill` (lists/jobs/fill.py), and this module is every read and write
of that row the lists app makes outside the kind's own slices. The
lifecycle IS the job's: open while READY or PROCESSING (the walk, then
the poll for its runs), DONE when every run settled, FAILED when a
worker found the config cannot run, CANCELLED when a user stopped it.
The wire's five words (the contract's FillStatusWire) derive from that
plus the runs (`status_of`).

Progress counters are NOT written here: the wire derives them from the
task rows and cell states at read time (services.fills.page_progress).

Split from the task state machine on purpose: node_runs.py is TASK
lifecycle (claim, settle, park), this is the fill's. Plain functions,
because none of this holds state: a fill id in, a queryset or a word
out, the writes delegated to JobService.Global. Not
account-scoped: the consumer is a trusted process serving every
account's fills, and the user-facing service resolves its fill
account-scoped before it calls cancel.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.db.models import QuerySet

from jobs.constants import OPEN_JOB_STATES, JobStatus
from jobs.models import Job
from jobs.services import JobService
from openbower_schema.fills import FillError, FillStatusWire

from ..constants import JOB_FAILURE_COPY

# The fill kind's name, restated here rather than imported from the
# kind module: the kind imports this module for its lifecycle, and the
# name is the one fact the two share.
FILL_KIND = "fill"

if TYPE_CHECKING:
    from ..jobs.fill import FillJob


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


def iter_consents(fills: QuerySet[Job]) -> Iterator[tuple[str, FillJob]]:
    """(fill id, its consent) for each job, TYPED: the one reader of a
    fill's payload outside the kind's own slices, so a field renamed on
    the kind fails loudly here rather than degrading to "no keys" at a
    guard. Lazy, single-pass, like every iter_."""
    # The kind imports this module for its lifecycle; the edge back is local.
    from ..jobs.fill import FillJob

    for fill_run_id, payload in fills.values_list("id", "payload"):
        yield str(fill_run_id), FillJob.model_validate(payload)


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


def status_of(job: Job, *, started: bool) -> FillStatusWire:
    """The wire's word for a fill job, derived and never stored (the
    contract's FillStatusWire is the one definition of the vocabulary):
    its terminal status as it is, an open one as `running` once a run
    has been claimed, else `pending`."""
    return word_of(job.status, started=started)


def error_of(status: str, code: str, message: str) -> FillError | None:
    """A FAILED fill's two-tier why, the one rule on every wire: the
    fill's own code with the copy it was failed with, or the runner's
    verdict under house copy (the stored cause is the operator's).
    Nothing for any other status, whatever the columns hold."""
    if status != JobStatus.FAILED:
        return None
    return FillError(code=code, message=JOB_FAILURE_COPY.get(code, message))


def word_of(status: str, *, started: bool) -> FillStatusWire:
    """The same derivation off a projected job status (the column
    summaries read columns, not rows)."""
    match JobStatus(status):
        case JobStatus.DONE:
            return "complete"
        case JobStatus.FAILED:
            return "failed"
        case JobStatus.CANCELLED:
            return "cancelled"
        case JobStatus.READY | JobStatus.PROCESSING:
            return "running" if started else "pending"
    # Exhaustive over JobStatus on purpose: a member added to the job's
    # lifecycle must be given a word here, never read as open by default.
    raise ValueError(f"no fill word for job status {status!r}")

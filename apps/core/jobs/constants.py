"""Bounds + enums for background jobs."""

from __future__ import annotations

from enum import StrEnum

JOB_KIND_MAX_LENGTH = 32
JOB_STATUS_MAX_LENGTH = 16
# One sentence of cause, bounded so a traceback can never become a row.
JOB_ERROR_MAX_LENGTH = 512
# The machine leg of a failed job's two-tier error (a kind's own code
# vocabulary), bounded like every stored code.
JOB_ERROR_CODE_MAX_LENGTH = 64

# How many UNEXPECTED exits (a raising slice, a dead tick) a job gets
# before it is failed with the last cause: the same patience a node run
# has. Running out of a tick's budget is not an attempt, and neither is
# a kind waiting on something else.
JOB_ATTEMPTS = 4
# How long one tick works before parking what it holds (binary): under
# the cron's minute so ticks never pile up, and well under the stale
# window so a live tick is never reclaimed.
JOB_TICK_BUDGET_SECONDS = 32
# How long the jobs service idles after a tick that found nothing due:
# the provisioners' cadence, so a job queued by a request is worked
# within seconds of the click.
JOB_LOOP_IDLE_SECONDS = 4
# A job PROCESSING longer than this was abandoned by a dead tick
# (binary, ~17 min); the next tick returns it to READY.
JOB_STALE_SECONDS = 1024
# The wait after a slice raised (binary): long enough that a failing
# job does not burn its attempts inside one minute.
JOB_RETRY_BACKOFF_SECONDS = 64


class JobFailureCode(StrEnum):
    """The runner's own verdicts on a job it failed (a kind's verdicts
    carry the kind's codes): a slice that kept raising, a tick that
    kept dying. Every FAILED job carries a code, so a reader never has
    to treat a blank one as a story."""

    CRASHED = "job_crashed"
    EXHAUSTED = "job_exhausted"


class JobStatus(StrEnum):
    """A job's lifecycle. READY is claimable (due once `scheduled_at`
    passes, which is how a kind waiting on something else parks);
    PROCESSING is held by a tick; DONE, FAILED and CANCELLED are
    terminal, the second carrying its cause in `error_code` and
    `error`, the third the record of a stop from outside."""

    READY = "ready"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# The states a job still owes work in: what a stop from outside can
# flip, what a kind-scoped "is it live" read counts.
OPEN_JOB_STATES = (JobStatus.READY, JobStatus.PROCESSING)

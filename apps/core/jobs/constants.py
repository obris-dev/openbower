"""Bounds + enums for background jobs."""

from __future__ import annotations

from enum import StrEnum

JOB_KIND_MAX_LENGTH = 32
JOB_STATUS_MAX_LENGTH = 16
# One sentence of cause, bounded so a traceback can never become a row.
JOB_ERROR_MAX_LENGTH = 512

# How many UNEXPECTED exits (a raising slice, a dead tick) a job gets
# before it is failed with the last cause: the same patience a node run
# has. Running out of a tick's budget is not an attempt.
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


class JobStatus(StrEnum):
    """A job's lifecycle. READY is claimable (due once `scheduled_at`
    passes); PROCESSING is held by a tick; DONE and FAILED are terminal,
    the second carrying its cause in `error`."""

    READY = "ready"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


NON_TERMINAL_JOB_STATES = (JobStatus.READY, JobStatus.PROCESSING)

"""What a job kind IS, and the pattern for adding one.

A kind is one class: a JobKind subclass whose class-level KIND names
it, whose fields are the job's PAYLOAD (what was asked, typed at
construction, so a payload can never be stored under another kind's
name), and whose `run` does ONE bounded slice of the work. The runner
owns the job's lifecycle (claiming it once across overlapping ticks,
counting attempts, parking it with its cursor, settling it, reclaiming
it, failing it at the cap); the kind owns nothing about that. It knows
how to do a slice and how to say where it stopped.

The contract with the runner is `run(job) -> dict | None`: do a slice
of work starting from `job.progress` (the kind's own cursor shape,
empty on the first slice), write outputs IDEMPOTENTLY (a reclaimed job
re-walks its last slice), and return the cursor to continue from, or
None when there is nothing left. A slice is a unit of time the runner
can afford to lose: one page, one file part, never the whole job.

To add a kind: a module in your app's `jobs` package, a JobKind
subclass, `register(...)` at the bottom. Enqueue with
`jobs.services.enqueue(account_id, YourKind(...))`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..models import Job


class JobKind(BaseModel):
    KIND: ClassVar[str]

    def run(self, job: Job) -> dict | None:
        """One slice from `job.progress`; the next cursor, or None when
        done. Every kind declares one."""
        raise NotImplementedError

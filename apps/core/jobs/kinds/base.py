"""What a job kind IS, and the pattern for adding one.

A kind is one class: a JobKind subclass whose class-level KIND names
it, whose fields are the job's PAYLOAD (what was asked, typed at
construction, so a payload can never be stored under another kind's
name), whose `Progress` is the typed shape of its resume cursor (the
same class the kind names as its type parameter, `JobKind[Progress]`,
so `run` is typed by the kind's OWN cursor at both edges), and whose
`run` does ONE bounded slice of the work. The runner owns the
job's lifecycle (claiming it once across overlapping ticks, counting
unexpected exits, parking it with its cursor, settling it, reclaiming
it, failing it at the cap); the kind owns nothing about that. It knows
how to do a slice, how to say where it stopped, and what to tidy when
it is stopped from outside.

The contract with the runner is `run(job, progress) -> Progress |
None`: do a slice of work from `progress` (the kind's own cursor, its
defaults on the first slice), write outputs IDEMPOTENTLY (a reclaimed
job re-walks its last slice), and return the cursor to continue from,
or None when there is nothing left. The two ways a slice LEAVES the
tick early are raised, as its last act, after its writes: `JobWaiting`
(nothing more can happen until something outside the job changes:
park for a while, no attempt spent, the cursor kept as stored unless
one is given) and `JobFailed` (the job ends FAILED with a code and copy
of the kind's own). Any other exception is an unexpected exit and
counts an attempt.
The runner parses the stored cursor through `Progress` at the claim
and dumps what `run` returns after every slice, so a cursor is typed
at both edges and a malformed one refuses at the parse instead of
walking from a wrong page. A slice is a unit of time the runner can
afford to lose: one page, one file part, never the whole job.

`on_stop(job)` runs inside the transaction of a stop from OUTSIDE (a
user's cancel, a worker failing the job for every row), before the
status flips: the place a kind tidies what it owns (a fill abandons
its queued runs). The default tidies nothing.

To add a kind: a module in your app's `jobs` package, a JobKind
subclass with a nested `Progress`, `register(...)` at the bottom.
Enqueue with `JobService(account_id=...).enqueue(YourKind(...), user_id=...)`, or
`enqueue_system(YourKind(...))` when no user asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..models import Job


class JobWaiting(Exception):
    """Raised by a slice, as its last act, when the job is waiting on
    something outside itself: park and wake after `seconds`. The
    cursor is kept as stored unless `progress` gives a new one (a poll
    that walked nothing has nothing to restate)."""

    def __init__(self, seconds: int, *, progress: BaseModel | None = None) -> None:
        super().__init__(f"waiting {seconds}s")
        self.seconds = seconds
        self.progress = progress


class JobFailed(Exception):
    """Raised by a slice to end the job FAILED on its own terms: the
    code is the kind's machine leg, the message its copy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JobKind[P: BaseModel](BaseModel):
    KIND: ClassVar[str]
    # The cursor's shape, the class named as P; every field defaulted,
    # since the first slice starts from an empty stored cursor. Held as
    # a class attribute too because the runner parses the stored cursor
    # through it at the claim, and a type parameter is not reachable at
    # runtime.
    Progress: ClassVar[type[BaseModel]]

    def run(self, job: Job, progress: P) -> P | None:
        """One slice from `progress`: the next cursor (more to do now)
        or None when done; raises JobWaiting or JobFailed to leave the
        tick early. Every kind declares one."""
        raise NotImplementedError

    def on_stop(self, job: Job) -> None:
        """Tidy what the kind owns when the job is stopped from
        outside, inside the stop's transaction, before the flip."""

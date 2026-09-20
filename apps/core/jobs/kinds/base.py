"""What a job kind IS, and the pattern for adding one.

A kind is one class: a JobKind subclass whose class-level KIND names
it, whose fields are the job's PAYLOAD (what was asked, typed at
construction, so a payload can never be stored under another kind's
name), whose `Progress` is the typed shape of its resume cursor, and
whose `run` does ONE bounded slice of the work. The runner owns the
job's lifecycle (claiming it once across overlapping ticks, counting
unexpected exits, parking it with its cursor, settling it, reclaiming
it, failing it at the cap); the kind owns nothing about that. It knows
how to do a slice, how to say where it stopped, and what to tidy when
it is stopped from outside.

The contract with the runner is `run(job, progress) -> Progress | Wait
| None`: do a slice of work from `progress` (the kind's own cursor,
its defaults on the first slice), write outputs IDEMPOTENTLY (a
reclaimed job re-walks its last slice), and return the cursor to
continue from, a `Wait` (the cursor plus how long to park before the
next slice, for a kind waiting on something outside the job: no
attempt is spent), or None when there is nothing left. A slice may
raise `JobFailed` to end the job FAILED with a code and copy of its
own; any other exception is an unexpected exit and counts an attempt.
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
Enqueue with `jobs.services.enqueue(account_id, YourKind(...))`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, NamedTuple

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..models import Job


class Wait(NamedTuple):
    """A slice's answer when the job is waiting on something outside
    itself: park with this cursor and wake after `seconds`."""

    seconds: int
    progress: BaseModel


class JobFailed(Exception):
    """Raised by a slice to end the job FAILED on its own terms: the
    code is the kind's machine leg, the message its copy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JobKind(BaseModel):
    KIND: ClassVar[str]
    # The cursor's shape; every field defaulted, since the first slice
    # starts from an empty stored cursor.
    Progress: ClassVar[type[BaseModel]]

    def run(self, job: Job, progress: BaseModel) -> BaseModel | Wait | None:
        """One slice from `progress`; the next cursor, a wait, or None
        when done. Every kind declares one."""
        raise NotImplementedError

    def on_stop(self, job: Job) -> None:
        """Tidy what the kind owns when the job is stopped from
        outside, inside the stop's transaction, before the flip."""

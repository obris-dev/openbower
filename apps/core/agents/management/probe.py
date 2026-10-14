"""What the operator probes (`manage.py providers`, `manage.py tools
--probe`) hand back to the shell that drives setup: an exit code per
state, so the caller branches on `$?` and never on wording. The codes
skip 1 (an uncaught boot failure, a traceback) and 2 (argparse usage),
which the command runner owns."""

from __future__ import annotations

from enum import IntEnum


class ProbeExit(IntEnum):
    OK = 0
    # providers: every source answered or was closed, and not one offers
    # a usable model (an empty roster is a server waiting for a pull).
    EMPTY = 3
    # Both commands: nothing to probe (no source in the file; a vendor
    # with no credentials).
    NOT_CONFIGURED = 4
    # tools --probe: the transport never got an answer.
    UNREACHABLE = 5
    # tools --probe: the vendor answered 429 or 5xx, reachable but not
    # usable right now.
    THROTTLED = 6
    # providers: no source answered at all (every one unreachable or
    # refused).
    DOWN = 7
    # tools --probe: the vendor decided against the call (a 4xx: a bad
    # key, a drained balance).
    REJECTED = 8


def reason(e: Exception) -> str:
    """One line naming an exception for an operator: the type alone
    when it carries no message, since `ReadTimeout: ` with nothing
    after it reads as a cut-off line."""
    text = str(e)
    return f"{type(e).__name__}: {text}" if text else type(e).__name__

"""The answerer's outward vocabulary for everything that is not a
completed call: typed exceptions, each carrying the call's FACTS
(CallRecord), never a verdict. The catcher (run_cell) owns the
meaning: which stored cell state a failure mode maps to, and later
which task state. `retriable` is the one flow-control fact a failure
declares about itself: whether asking again later could change the
answer (a throttle passes; a model that cannot speak the schema will
not start to).

Two refusals sit beside the failures: EmptyRender (the row asked
nothing, so there is no call and no record) and NoAvailableTools
(tools asked for with none available, refused before any spend; its
record's seeded statuses are the why). ModelUnavailable is NOT
here: an unrunnable address is config tier and fails the fill, so it
raises from the providers seam and propagates untouched."""

from __future__ import annotations

from typing import ClassVar

from .record import CallRecord


class EmptyRender(Exception):
    """The prompt rendered EMPTY for this row (every {{token}} blank:
    the one all-blank row a config can legitimately produce), so there
    is nothing to ask, nothing to spend, and no record to carry. ROW
    tier, not config: the run maps it to a terminal NO_EVIDENCE blank,
    never a fill failure."""


class NoAvailableTools(Exception):
    """Tools were asked for and none is available, so the call can
    never produce evidence and is REFUSED before a completion is
    bought. The record rides the refusal: its seeded statuses are the
    why, and the run's naming walk reads the blame off them per
    tool."""

    def __init__(self, record: CallRecord) -> None:
        super().__init__("tools toggled but none available")
        self.record = record


class AgentError(Exception):
    """Base for every way the CALL ITSELF failed (as opposed to a call
    that completed with a blank verdict, which returns an Answer).
    Raised bare only for failures no subclass names: the unknown is
    fatal, because retrying what we cannot diagnose re-buys it."""

    retriable: ClassVar[bool] = False

    def __init__(self, record: CallRecord, detail: str = "") -> None:
        super().__init__(detail or type(self).__name__)
        self.record = record


class AgentRateLimited(AgentError):
    """The model's provider said slow down (429): infrastructure tier, the
    row is worth re-buying once the window passes."""

    retriable = True


class AgentOverloaded(AgentError):
    """The model's provider fell over (5xx): indistinguishable from a
    throttle in consequence, retried the same way."""

    retriable = True


class AgentTimeout(AgentError):
    """The model said nothing in time: indistinguishable from an
    overloaded server, retried like one."""

    retriable = True


class AgentUnreachable(AgentError):
    """The call never completed over the wire (connection refused, DNS,
    a dropped socket): the model never spoke, so nothing about this
    config is settled and asking again later could reach it."""

    retriable = True


class AgentUnableToRespond(AgentError):
    """The request/tool budget was spent and no verdict landed: the
    model's behavior under this config, so the blank is SETTLED (the
    same config re-buys the same refusal), not infrastructure."""


class AgentResponseInvalid(AgentError):
    """The framework's validation retry ran dry: the model spoke, but
    never in the output type. Settled for this config; the fix is a
    more capable model, not a retry."""

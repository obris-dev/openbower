"""Wire contract for node runs read by id: the agent builder's bench.

A bench run is ONE run of a drafted config against one hand-fed row,
owning its input and landing its result on itself. The builder POSTs
it, polls it to a terminal status, and renders the stored result; the
rest of the run ledger (a fill's rows, a webhook's digests) is read
through its own surfaces and never by run id.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .fills import CellRunResult

# The run ledger's status vocabulary, the server's NodeRunStatus
# (parity pinned): the four open states, then the terminals.
NodeRunStatusWire = Literal[
    "ready", "queued", "processing", "deferred", "done", "abandoned", "row_missing", "list_missing"
]
# The OPEN partition: a poll keeps polling while the status is one of
# these. A wire fact (x-constants) so the client derives the loop
# predicate instead of retyping it.
OPEN_NODE_RUN_STATES: tuple[NodeRunStatusWire, ...] = ("ready", "queued", "processing", "deferred")


class NodeRunWire(BaseModel):
    """One run, read by id (GET /v1/runs/{id}), and the echo of the
    bench's POST and cancel."""

    id: str
    status: NodeRunStatusWire
    result: CellRunResult | None = Field(
        default=None,
        description="The run's stored result, served once it FINISHED with one (a cancel racing the "
        "last landing must not strand a paid diagnosis); None while the run is open, and for a run "
        "that ended without running (abandoned, or its config could not run).",
    )
    heartbeat_at: str = Field(
        description="The run's latest state change; the client judges staleness against "
        "ROW_LEASE_STALE_SECONDS off the wire, warning-role only (never presented as failure)."
    )
    created_at: str

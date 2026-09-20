"""What a node processor IS: the standardized contract every kind's
processor fulfils, so a walker can offer rows to any node without
knowing its kind, and a dispatcher can execute any node's runs without
knowing how.

Two halves. WHICH rows are owed a run, asked per page: `enqueue_runs`
takes the rows in scope, reads whatever inputs the kind's judgement
needs (its own reads, batched as it sees fit: cell records over columns
for a webhook barrier or a refill, nothing at all for a fresh fill),
decides which rows are owed a run, inserts those runs under the open-run
key so a row offered twice is a no-op whichever walker offered it, and
reports how many it queued. The walker pages the rows in scope, hands
them over, advances its cursor; it knows no kind and no column. The
judgement is per page, not per row, because a kind's read is the
expensive part and it batches.

HOW a run executes, in the shape the kind's runs travel: `process_run`
for a kind whose runs are claimed one at a time off the topic (the
consumer hands over the claimed run), `process_batch` for a kind that
claims and settles a node's due runs together (the flush hands over the
node). A kind overrides exactly one; the other keeps its raising
default, so a dispatcher holding the wrong shape fails loudly instead of
silently doing nothing.

A processor is constructed for ONE node of its kind, account-scoped,
with the WALK SCOPE that says what this pass is for (a fresh fill under
a fill job, the remaining rows of a refill, the rows a push appended, a
structural backfill); a kind reads the parts of the scope it cares
about and ignores the rest. Execution ignores the scope: a run carries
its own identity."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel

from ..models import List, ListRow, Node, NodeRun
from ..services.node_runs import NodeRunFlow


class RunOutcome(StrEnum):
    """What `process_run` did with the claimed run, for the consumer's
    log and its tests: settled terminally (with or without a value),
    parked for a retry, or retired because its row or list vanished."""

    DONE = "done"
    PARKED = "parked"
    ROW_MISSING = "row_missing"
    LIST_MISSING = "list_missing"


@dataclass
class BatchTally:
    """What `process_batch` did with a node's due runs: runs, not
    digests (one digest carries many runs). `skipped` counts a NODE the
    gates turned away without claiming."""

    sent: int = 0
    parked: int = 0
    failed: int = 0
    skipped: int = 0


class WalkMode(StrEnum):
    """What a pass over rows is FOR. The agent kind judges each mode by
    a different rule; the webhook kind judges every mode the same way."""

    # A fresh fill: every row the prompt can act on, under a fill job.
    FRESH = "fresh"
    # A refill: the rows still blank in the walked columns, and not
    # already owed by the run being resumed, under a fill job.
    REMAINING = "remaining"
    # Rows a push appended: the node runs unless the push filled every
    # column it owns. No fill job.
    PUSHED = "pushed"
    # A structural walk over the whole sheet (a webhook column added or
    # its wait set changed). No fill job.
    BACKFILL = "backfill"


class WalkScope(BaseModel):
    """The typed context of one pass, carried on the walker's payload
    and handed to the processor at construction."""

    mode: WalkMode = WalkMode.BACKFILL
    # The fill job the runs belong to, for FRESH and REMAINING; "" otherwise.
    fill_run_id: str = ""
    # The stopped fill a REMAINING walk resumes: rows it still owed are
    # the only ones offered. "" = the column's whole remainder.
    owed_by: str = ""
    # The columns a REMAINING walk judges owed-ness across: the one
    # column the user clicked for a widening gesture, the resumed
    # fill's whole set for a Continue. Empty = the fill's own columns.
    column_keys: list[str] = []
    # A fill's CONSENT RANGE: rows at or below this position (0 = no
    # bound). Positions are dense and append-only, so a row appended
    # after the click sits above it and is never walked.
    until_position: int = 0
    # A scoped fill's first N qualifying rows (0 = every qualifying row).
    limit: int = 0


class NodeProcessor(ABC):
    KIND: ClassVar[str]

    def __init__(self, *, account_id: str, node: Node, scope: WalkScope) -> None:
        self.account_id = account_id
        self.node = node
        self.scope = scope

    @abstractmethod
    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime) -> int:
        """Queue a run for every row among `rows` this node owes one to,
        under the open-run key, and return how many were queued. Reads
        its own inputs for the page; born in the state the kind's lane
        expects."""

    def process_run(self, task: NodeRun, *, flow: NodeRunFlow) -> RunOutcome:
        """Execute ONE run of this node that the caller already claimed
        (PROCESSING, the attempt stamped) and settle it through `flow`.
        Raises ListNotFound / RowNotFound as they are when the sheet
        vanishes mid-landing: the consumer's story to resolve."""
        raise NotImplementedError(f"{self.KIND} runs are not executed one at a time")

    def process_batch(self, *, flow: NodeRunFlow, now: datetime) -> BatchTally:
        """Claim this node's due runs and execute them as one unit,
        settling each by what came back."""
        raise NotImplementedError(f"{self.KIND} runs are not executed as a batch")

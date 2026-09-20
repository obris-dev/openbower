"""What a node processor IS: the standardized contract every kind's
processor fulfils, so a walker can offer rows to any node without
knowing its kind.

ONE question, asked per page: `enqueue_runs` takes the rows in scope,
reads whatever inputs the kind's judgement needs (its own reads, batched
as it sees fit: cell records over columns for a webhook barrier or a
refill, nothing at all for a fresh fill), decides which rows are owed a
run, inserts those runs under the open-run key so a row offered twice
is a no-op whichever walker offered it, and reports how many it
queued. The walker pages the rows in scope, hands them over, advances
its cursor; it knows no kind and no column. The judgement is per page,
not per row, because a kind's read is the expensive part and it
batches.

A processor is constructed for ONE node of its kind, account-scoped,
with the WALK SCOPE that says what this pass is for (a fresh fill under
a Fill, the remaining rows of a refill, the rows a push appended, a
structural backfill); a kind reads the parts of the scope it cares
about and ignores the rest."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel

from ..models import List, ListRow, Node


class WalkMode(StrEnum):
    """What a pass over rows is FOR. The agent kind judges each mode by
    a different rule; the webhook kind judges every mode the same way."""

    # A fresh fill: every row the prompt can act on, under a Fill.
    FRESH = "fresh"
    # A refill: the rows not yet settled under this config, and not
    # already owed by the run being resumed, under a Fill.
    REMAINING = "remaining"
    # Rows a push appended: the node runs unless the push filled every
    # column it owns. No Fill.
    PUSHED = "pushed"
    # A structural walk over the whole sheet (a webhook column added or
    # its wait set changed). No Fill.
    BACKFILL = "backfill"


class WalkScope(BaseModel):
    """The typed context of one pass, carried on the walker's payload
    and handed to the processor at construction."""

    mode: WalkMode = WalkMode.BACKFILL
    # The Fill the runs belong to, for FRESH and REMAINING; "" otherwise.
    fill_run_id: str = ""
    # The stopped fill a REMAINING walk resumes: rows it still owed are
    # the only ones offered. "" = the column's whole remainder.
    owed_by: str = ""
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

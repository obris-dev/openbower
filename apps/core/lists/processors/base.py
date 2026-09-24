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

HOW a run executes, in the shape the kind's runs travel: `_process_run`
for a kind whose runs are claimed one at a time off the topic (the
consumer hands over the claimed run), `_process_batch` for a kind that
executes a node's due runs together (the flush claims them and hands
over the batch). The dispatchers CLAIM; the kinds EXECUTE. A kind overrides exactly one; the other keeps its raising
default, so a dispatcher holding the wrong shape fails loudly instead of
silently doing nothing. The dispatchers call the PUBLIC pair,
`process_run` and `process_batch`, which run the kind's half and then
the one thing every kind owes the workflow after a run reaches DONE on
a row: the ADVANCE (services/workflow_reactions.py), which moves the workflow one step for it
node behind a barrier this node's path feeds. A kind never calls it and
cannot forget it.

A processor is constructed for ONE node of its kind, account-scoped,
with the WALK SCOPE that says what this pass is for (a fresh fill under
a fill job, the remaining rows of a refill, the rows a push appended, a
structural backfill); a kind reads the parts of the scope it cares
about and ignores the rest. Execution ignores the scope: a run carries
its own identity."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel

from ..cells import CellWrite
from ..models import List, ListRow, Node, NodeRun
from ..services.node_runs import NodeRunFlow


class RunOutcome(StrEnum):
    """What `process_run` did with the claimed run, for the base's
    advance, the consumer's log, and the tests. LANDED: the run WROTE
    its row (a value, or a diagnosis), so the workflow may have more to
    do for that row. EXITED: the run ended and wrote nothing the
    workflow should act on (skipped unrun, a config that cannot run, a
    preview landing on itself, a crash past its attempts). PARKED: a
    retry later. ROW_MISSING | LIST_MISSING: retired, its subject gone."""

    LANDED = "landed"
    EXITED = "exited"
    PARKED = "parked"
    ROW_MISSING = "row_missing"
    LIST_MISSING = "list_missing"


@dataclass
class BatchTally:
    """What `_process_batch` did with a node's due runs: runs, not
    batches (one batch carries many runs). `settled` is the runs that
    reached DONE with their work done, and `settled_rows` those runs'
    (list id, row id) pairs, for the advance; `skipped` counts a NODE
    the gates turned away without claiming."""

    settled: int = 0
    parked: int = 0
    failed: int = 0
    skipped: int = 0
    settled_rows: list[tuple[str, str]] = field(default_factory=list)

    def __add__(self, other: BatchTally) -> BatchTally:
        """What two steps did, together: a kind's batch is the sum of
        what each of its steps reports, never one tally threaded
        through them."""
        return BatchTally(
            settled=self.settled + other.settled,
            parked=self.parked + other.parked,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
            settled_rows=[*self.settled_rows, *other.settled_rows],
        )


class FillMode(StrEnum):
    """What OCCASIONED this pass over rows, never the door it came
    through: the same occasion reaches a node from more than one caller,
    and a mode named for a caller is already wrong for the others. The
    agent kind judges each occasion by its own rule; the webhook kind
    judges every one of them the same way."""

    # A fresh fill: every row the prompt can act on, under a fill job.
    FRESH = "fresh"
    # A refill: the rows still blank in the walked columns, and not
    # already owed by the run being resumed, under a fill job.
    REMAINING = "remaining"
    # The sheet moved on its own, so the node judges what it is missing:
    # rows arrived at the sheet, or a barrier ahead of the node cleared.
    # The node runs unless every column it fills already holds a value.
    # No fill job, so its runs ride the autofill lane (fill_run_id NULL).
    AUTOFILL = "autofill"
    # A structural walk over the whole sheet (a webhook column added or
    # its wait set changed): a node that just changed, caught up on the
    # rows that already exist. No fill job.
    BACKFILL = "backfill"


class FillScope(BaseModel):
    """The typed context of one pass, carried on the walker's payload
    and handed to the processor at construction."""

    mode: FillMode = FillMode.BACKFILL
    # The fill job the runs belong to, for FRESH and REMAINING; "" otherwise.
    fill_run_id: str = ""
    # The stopped fill a REMAINING walk resumes (Continue): the rows it
    # still owed, its ABANDONED runs, are the only ones offered. "" =
    # the column's whole remainder.
    resumed_fill_id: str = ""
    # The columns this pass judges across, DECIDED BY THE STARTER and
    # never read off the sheet by the processor: for a REMAINING walk
    # the one column the user clicked (a widening gesture) or the
    # resumed fill's whole set (Continue); for an AUTOFILL pass the
    # columns the node fills on the sheet, as the reaction read them.
    # Empty for the modes that judge by no column.
    column_keys: list[str] = []


# The ListRow fields queuing ANY run reads: a run is stamped with its
# row's rank so the pickers take runs in sheet order. The base's, not a
# kind's to repeat or forget. The id is the primary key, which Django
# never defers, so it is never missing.
_RUN_ROW_FIELDS: tuple[str, ...] = ("rank",)


class NodeProcessor(ABC):
    KIND: ClassVar[str]
    # The ListRow fields THIS KIND's judgement reads off a row it is
    # handed, beyond the ones queuing any run reads (the base's own,
    # below, added for every kind whatever a kind declares).
    REQUIRED_ROW_FIELDS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, *, account_id: str, node: Node, scope: FillScope) -> None:
        self.account_id = account_id
        self.node = node
        self.scope = scope

    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime, limit: int = 0) -> int:
        """The walkers' call: the page's row_fields() loaded first,
        in one read, then the kind's `_enqueue_runs`. A caller may hand
        rows loaded with less than a kind reads (the advance loads ids
        and ranks only), and a deferred field loads one instance at a
        time the first time it is touched, so a kind left to Django would
        pay a query per row without asking for one."""
        if not rows:
            return 0
        rows = self._hydrate_required_fields(rows)
        return self._enqueue_runs(target_list, rows, now=now, limit=limit)

    @abstractmethod
    def _enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime, limit: int = 0) -> int:
        """Queue a run for every row among `rows` this node owes one to,
        under the open-run key, and return how many were queued; with
        `limit`, stop at that many, judging no further (0 = every owed
        row in `rows`). Reads its own inputs for the page beyond the row
        fields it declares; born in the state the kind's lane expects."""

    @classmethod
    def _hydrate_required_fields(cls, rows: Sequence[ListRow]) -> Sequence[ListRow]:
        """The page with every declared field loaded, in ONE read, for
        the rows a caller handed without them. Assigning a deferred
        field populates it with no query of its own."""
        required = cls.row_fields()
        thin = [row for row in rows if any(name in row.get_deferred_fields() for name in required)]
        if not thin:
            return rows
        ids = [row.id for row in thin]
        loaded = {values[0]: values[1:] for values in ListRow.objects.filter(id__in=ids).values_list("id", *required)}
        for row in thin:
            for name, value in zip(required, loaded[row.id], strict=True):
                setattr(row, name, value)
        return rows

    @classmethod
    def row_fields(cls) -> tuple[str, ...]:
        """Every field a row handed to this kind must carry: the ones any
        run reads, then the kind's own, each once."""
        combined = (*_RUN_ROW_FIELDS, *cls.REQUIRED_ROW_FIELDS)
        return tuple(dict.fromkeys(combined))

    def process_run(self, task: NodeRun, *, flow: NodeRunFlow) -> RunOutcome:
        """The consumer's call: the kind's `_process_run`, then the
        workflow advanced for a run that LANDED on its row (an exited,
        parked, or retired run advances nothing). The advance runs
        AFTER the kind's own transaction: it inserts under the open-run
        key, so a repeat is a no-op, and a crash between the two is
        closed by re-offering, never by ordering."""
        outcome = self._process_run(task, flow=flow)
        if outcome is RunOutcome.LANDED:
            self._reactions().advance(list_id=task.list_id, row_ids=[task.row_id], from_node_id=task.node_id)
        return outcome

    def process_batch(self, tasks: Sequence[NodeRun], *, flow: NodeRunFlow, now: datetime) -> BatchTally:
        """The flush's call: the kind's `_process_batch` over the runs
        the flush already claimed (PROCESSING, the attempt stamped),
        then the advance for the rows it settled, one page per list (a
        node's runs are one sheet's, so that is one page)."""
        tally = self._process_batch(tasks, flow=flow, now=now)
        by_list: dict[str, list[str]] = {}
        for list_id, row_id in tally.settled_rows:
            by_list.setdefault(list_id, []).append(row_id)
        for list_id, row_ids in by_list.items():
            self._reactions().advance(list_id=list_id, row_ids=row_ids, from_node_id=str(self.node.id))
        return tally

    def _reactions(self):
        # Function-local: the reactions import the processors (they
        # offer rows to them), and this is the one edge back.
        from ..services.workflow_reactions import WorkflowReactions

        return WorkflowReactions(account_id=self.account_id)

    def on_run_landed(self, column_keys: Sequence[str], outcome: object) -> list[CellWrite]:
        """What one of this node's runs does to its cells: one write per
        column the node fills on the sheet, from the kind's own outcome
        (the agent's CellRunResult as AnsweredWrites, the webhook's SENT |
        FAILED as a WebhookWrite). Pure; the landing persists it. A kind
        whose runs touch no cell (a barrier) keeps the raising default."""
        raise NotImplementedError(f"{self.KIND} runs land on no cell")

    def _process_run(self, task: NodeRun, *, flow: NodeRunFlow) -> RunOutcome:
        """Execute ONE run of this node that the caller already claimed
        (PROCESSING, the attempt stamped) and settle it through `flow`.
        Raises ListNotFound / RowNotFound as they are when the sheet
        vanishes mid-landing: the consumer's story to resolve."""
        raise NotImplementedError(f"{self.KIND} runs are not executed one at a time")

    def _process_batch(self, tasks: Sequence[NodeRun], *, flow: NodeRunFlow, now: datetime) -> BatchTally:
        """Execute a batch of this node's runs that the caller already
        claimed (PROCESSING, the attempt stamped) as one unit, settling
        each through `flow` by what came back and naming the rows it
        settled."""
        raise NotImplementedError(f"{self.KIND} runs are not executed as a batch")

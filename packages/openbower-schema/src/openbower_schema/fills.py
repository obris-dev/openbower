"""Wire contract for column fills (the AI-columns domain).

A fill RUN is a durable background walk of a sheet: one agent run
per row, cells written where blank, every blank carrying its cause. The
queue is materialized at admission, one task per consented row, and a
cell reads PENDING because a queued task on a live fill says so, so
the wire speaks fill run envelopes, per-cell states, and nothing
about workers. Copy a user reads says "fill", the feature's own
word; "run" names the record in TYPE names (FillRunWire,
FillRunPage), so prose can tell one run of a fill from the feature
itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .agents import MAX_TOOL_CALLS, TestSearch
from .lists import WireCellState as WireCellState

FillStatusWire = Literal["pending", "running", "complete", "failed", "cancelled"]

# The SETTLED partition of WireCellState: diagnoses the same config would
# just reproduce, so they hold (and never re-spend) until the config
# changes; every other cause re-runs on the next fill. A wire fact
# (x-constants) so the client derives the partition instead of
# hand-retyping it beside its copy.
SETTLED_CELL_STATES: tuple[WireCellState, ...] = (
    "no_evidence",
    "no_answer",
    "unverified",
    "unparseable",
    "type_mismatch",
)

# Per-row retry patience: the initial run plus 3 retries (binary). The
# consent footer's "worst case 4x these counts" clause is this number,
# so it is a wire fact, not a server internal.
FILL_ROW_ATTEMPTS = 4
# The row-lease staleness window: a claimed row's lease is renewed in
# bulk by the worker's supervising loop, which passes far more often
# than this, so silence past this window means the CLAIMANT IS GONE (a
# killed process, a dead thread), not that a row is merely slow. It
# measures process death, NOT how long a row may take: one run's worst
# case (the runtime's TEST_RUN_WORST_CASE_SECONDS) is several multiples
# of this number, and a running row keeps its lease renewed the whole
# way. The client's heartbeat warning judges against it.
ROW_LEASE_STALE_SECONDS = 256
# Admission budget for the FREE search door (derived): MAX_TOOL_CALLS
# searches per row times the largest sheet the free door should carry
# end to end (128 rows, binary). Fills needing more are refused at
# admission, naming the paid door.
FREE_SEARCH_FILL_BUDGET = MAX_TOOL_CALLS * 128


class CellRunResult(BaseModel):
    """What ONE row's run produced, stored verbatim on the task that
    ran it: the cells it would write ({} = nothing, honestly), the
    evidence the model saw, each query's diagnosis, and the causes
    behind any blank.

    ONE shape for both writers. A row can be answered by the worker or
    borrowed from a bench run that already answered it, and both land
    in the same column, so a reader that had to ask which writer
    produced a record would be reading two contracts through one field.
    The bench's wire view (AgentTestResult) is a NARROWER projection of
    this, not a second version of it.

    What LANDED is the sheet row plus its cell states; the difference
    between the two is the audit story (an answer write-if-blank
    refused, a value the confidence floor dropped, a shape the column
    would not take), which is why this is stored whole rather than
    reduced to what survived."""

    cells: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    searches: list[TestSearch] = Field(default_factory=list)
    # WHY cells is empty ("" when cells landed), and the cause an
    # UNANSWERED output carries when the run answered others. Both are
    # WireCellState values; both default blank because a fully answered run
    # has neither.
    blank_cause: str = ""
    declined_cause: str = ""
    # key -> the model's confidence and the reason it gave, for every
    # answered output INCLUDING the ones the floor discarded. The only
    # place the rejected distribution exists.
    assessments: dict[str, Any] = Field(default_factory=dict)
    # tool -> its door's status code at the end of the run, for every
    # toggled tool ("open" when it served). The record the cell state's
    # `tools` is copied from; a run before tools reported statuses
    # stores nothing here.
    tools: dict[str, str] = Field(default_factory=dict)


class FillError(BaseModel):
    """A failed fill's two-tier why: `code` is the machine leg (client
    branching), `message` is server-authored copy rendered verbatim."""

    code: str
    message: str


class FillCounters(BaseModel):
    """Worker-written progress (never COUNT(*) polling): attempted is
    rows with a terminal outcome this fill; blank counts diagnosed
    blanks; transient counts rows currently parked in retry."""

    attempted: int
    filled: int
    blank: int
    transient: int
    # The pace facts (defaults for fills from before they existed):
    # accumulated wall seconds across terminal rows, the share of it
    # spent parked on search, and the AIMD operating point at the last
    # write. "What is slow" derives client-side from the ratio.
    row_seconds: int = 0
    search_wait_seconds: int = 0
    # NOT `concurrency`: that name already means a ceiling in two other
    # places (the fill's downward-only override, a source's declared
    # limit). This is the AIMD operating point UNDER those, and it
    # mirrors the column of the same name.
    concurrency_point: int = 0


class FillRunWire(BaseModel):
    """The fill run envelope. The POST and cancel ECHOES carry every
    state (a failed run is an API object with its error, not a 4xx);
    the fills LIST the sheet re-attaches to carries live runs only
    (see FillRunPage)."""

    id: str
    list_id: str
    agent_id: str
    status: FillStatusWire
    column_keys: list[str] = Field(description="The columns this run owns, frozen at consent.")
    counters: FillCounters
    confirmed_row_count: int = Field(
        description="Rows this run TARGETED, fixed when it opened: the progress denominator. "
        "The consent echo is a REQUEST field of the same name that admission compares against "
        "the sheet, 409ing on drift; what ships here is what the walk actually consented to, "
        "which a scoped fill makes smaller than the sheet."
    )
    started_by: str = Field(description="User id, ATTRIBUTION only; authorization is account membership.")
    heartbeat_at: str | None = Field(
        default=None,
        description="Stamped with each counter write; the client judges staleness against "
        "ROW_LEASE_STALE_SECONDS off the wire, warning-role only (never presented as failure).",
    )
    error: FillError | None = Field(
        default=None,
        description="This run's error, both legs (tier 1: the message renders verbatim); "
        "None unless the run FAILED, the same predicate ColumnFillSummary.last_error states.",
    )
    # The config snapshot frozen at admission stays STORED, not wired:
    # nothing renders it on a poll, and a run-detail read is where it
    # belongs when a surface needs it.
    created_at: str
    updated_at: str


class ColumnFillSummary(BaseModel):
    """Per-column coverage, computed server-side so the client renders
    instead of reconstructing (a client sum over one PAGE of fills
    silently undercounts the moment history outgrows the page).

    Deliberately NOT carrying how many rows a refill would target: that
    is planning-grade math on a four-second progress poll. It is asked
    once, on the consent path, where it has to be exact anyway."""

    column_key: str
    current_fill_id: str = Field(description='The newest fill run naming this column; "" when none is exposed.')
    # No defaults on either field, the sibling current_fill_id's rule:
    # a constructor that forgets one must fail loudly, because the
    # defaults are real stories ("never ran", "no failure") that would
    # otherwise ship silently.
    current_status: FillStatusWire | Literal[""] = Field(
        description='The status of the run current_fill_id names; "" when the column has never run. '
        "The page's runs list is LIVE runs only, so this is where a terminal story lands.",
    )
    last_error: FillError | None = Field(
        description="The newest run's error, both legs (tier 1: the message renders verbatim); "
        "None unless that run FAILED, so a newer clean run clears it and a stopped run carries none.",
    )
    filled: int = Field(description="Cells in this column that hold a value.")
    attempted: int = Field(
        description="Cells this column's fills have RESOLVED: filled plus diagnosed blanks. A targeted "
        "cell ends in exactly one of those two places, so their sum is what the column was asked to do. "
        "It is the honest denominator for filled; the sheet's row count is a different question."
    )


class FillRunPage(BaseModel):
    """LIVE runs plus the per-column summaries. Terminal runs do not
    ride the poll: a finished run's story (its status, its error) lands
    on the column summary the moment it leaves this list, so the page
    carries the in-flight work and the summaries carry everything a
    column needs to say about its past."""

    runs: list[FillRunWire] = Field(
        description="LIVE runs only. Named for what it holds rather than the house `items`, "
        "because this page carries a second collection (`columns`) and `items` beside it "
        "would name neither."
    )
    columns: list[ColumnFillSummary] = Field(
        default=[], description="One summary per AI column of the list this page belongs to."
    )
    next_cursor: str | None = Field(default=None, description="The last id when more runs exist.")


class ColumnPromptWire(BaseModel):
    """The column's CURRENT fill config as the server holds it (GET),
    and the echo after a column-scoped edit (PATCH
    /lists/{id}/columns/{key}/prompt). Live fills keep their frozen
    snapshot; an edit reaches the NEXT fill's admission, so surfaces
    peeking at "what fills this column" read HERE, never a fill's
    snapshot."""

    prompt: str
    model: str
    source: str

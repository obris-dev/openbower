"""Bounds + enums for the lists domain."""

from __future__ import annotations

from enum import StrEnum

from openbower_kernel.provider_config import MAX_FILL_CONCURRENCY
from openbower_schema.fills import (
    FILL_ROW_ATTEMPTS as FILL_ROW_ATTEMPTS,
)
from openbower_schema.fills import (
    FREE_SEARCH_FILL_BUDGET as FREE_SEARCH_FILL_BUDGET,
)
from openbower_schema.fills import (
    ROW_LEASE_STALE_SECONDS as ROW_LEASE_STALE_SECONDS,
)
from openbower_schema.lists import (
    COLUMN_KEY_MAX_LENGTH as COLUMN_KEY_MAX_LENGTH,
)
from openbower_schema.lists import (
    COLUMN_LABEL_MAX_LENGTH as COLUMN_LABEL_MAX_LENGTH,
)

LABEL_MAX_LENGTH = 120
# Rows per list: sized to hold a full confident lead list.
MAX_LIST_ROWS = 50_000
# Per-cell character ceiling, deliberately above the incumbent
# spreadsheets (Sheets 50,000, Excel 32,767): our sheet is not bounded
# by theirs, at the honest cost that a maxed cell truncates if an
# exported CSV lands back in those tools.
CELL_MAX_LENGTH = 65_536
MAX_ROWS_PER_ADD = 1000
DEFAULT_ROWS_PAGE = 50
MAX_ROWS_PAGE = 200
DEFAULT_INDEX_PAGE = 50
MAX_INDEX_PAGE = 200
MAX_LIST_COLUMNS = 70
# Bounds the unpaged folder GET (the whole taxonomy ships at once).
MAX_FOLDERS = 200
# CSV uploads: a whole-CRM export fits comfortably; anything bigger is
# probably not a lead list.
MAX_CSV_BYTES = 5 * 1024 * 1024
# Multipart framing headroom over the file itself (the Content-Length
# pre-check fires before the body is parsed).
IMPORT_BODY_OVERHEAD = 16 * 1024

ORIGIN_MAX_LENGTH = 16


class ListOrigin(StrEnum):
    """How a list came to exist (an index-page chip, and the tell for
    which columns to expect)."""

    DISCOVER = "discover"
    CSV = "csv"
    MANUAL = "manual"


class ColumnType(StrEnum):
    """A column's sheet type: spreadsheet vocabulary a user already
    knows. Types drive RENDERING only (url cells link, numbers align
    right); no behavior branches on them."""

    TEXT = "text"
    NUMBER = "number"
    CURRENCY = "currency"
    DATE = "date"
    URL = "url"
    EMAIL = "email"


# Fill machinery bounds. Wire facts (the client reads them off
# x-constants) live on the CONTRACT and are re-exported here; the rest
# are server internals, binary when invented, derivations stated when
# derived.
# Rows per claim: MAX_FILL_CONCURRENCY, derived, so one claim can
# fill every slot a process owns. claim_batch additionally never
# takes more than the claimant's FREE AIMD slots: only a running row
# crosses the seams that renew its lease, so a queued-but-claimed row
# would read dead while merely waiting.
FILL_CLAIM_BATCH = MAX_FILL_CONCURRENCY
# Where a hosted (canonical) source's AIMD controller starts (binary);
# self-hosted sources start at 1 and never probe past their declared
# ceiling (only the operator can see that box).
FILL_CONCURRENCY_HOSTED_START = 4
# Live fills per ACCOUNT (binary). The bench lane's account-level
# admission reads this same constant so the two lanes cannot drift;
# the bench keeps its own per-process semaphore as a backstop.
MAX_ACTIVE_FILLS = 4
# Consecutive rows parked for retry that fail the FILL config-tier
# (binary): per-row attempts are patience for flaky moments, this
# breaker is across-row detection of a dead or throttling provider, the
# model's or a tool's search provider alike (a search provider that keeps
# refusing parks its rows exactly as a throttling model does).
CONSECUTIVE_TRANSIENT_LIMIT = 8

FILL_STATUS_MAX_LENGTH = 16
FILL_TASK_STATUS_MAX_LENGTH = 16
CELL_STATE_MAX_LENGTH = 32
# The failed fill's two-tier error: code is the machine leg, message is
# server-authored copy rendered verbatim (bounded like every authored
# value).
FILL_ERROR_CODE_MAX_LENGTH = 64
FILL_ERROR_MESSAGE_MAX_LENGTH = 256
# The claimant's identity stamp (hostname:pid); diagnostic, bounded.
LEASED_BY_MAX_LENGTH = 128
# A config's sha256 hex digest (agents.services.config_fingerprint).
CONFIG_FINGERPRINT_MAX_LENGTH = 64


# Stable error codes for the column and fill lanes' admission
# refusals: the machine leg of the {error, detail} envelope both lanes
# answer with (the detail is server-authored copy the client renders
# verbatim, tier 1). A leg both lanes can refuse under (reserved_key,
# columns_full) is ONE member on purpose: the same refusal must never
# reach a client under two spellings.
class FillErrorCode(StrEnum):
    FILL_REFUSED = "fill_refused"
    COLUMN_REFUSED = "column_refused"
    FILL_ACTIVE = "fill_active"
    FILLS_FULL = "fills_full"
    ROW_COUNT_CHANGED = "row_count_changed"
    CONFIG_CHANGED = "config_changed"
    RESUME_NOT_FOUND = "resume_not_found"
    EMPTY_FILL = "empty_fill"
    NO_ELIGIBLE_ROWS = "no_eligible_rows"
    REFILL_EMPTY = "refill_empty"
    # The column is on the sheet but its agent stopped declaring an
    # output that lands there.
    FILL_COLUMN_RETIRED = "fill_column_retired"
    FREE_SEARCH_BUDGET = "free_search_budget"
    COLUMN_COLLISION = "column_collision"
    COLUMN_TYPE_CHANGED = "column_type_changed"
    COLUMN_EXISTS = "column_exists"
    COLUMN_ORDER_STALE = "column_order_stale"
    COLUMN_KEYS_NOT_UNIQUE = "column_keys_not_unique"
    COLUMN_AGENT_MISSING = "column_agent_missing"
    DERIVED_KEY_COLLISION = "derived_key_collision"
    RESERVED_KEY = "reserved_key"
    COLUMNS_FULL = "columns_full"
    PROVIDER_RETIRED = "provider_retired"
    MODEL_UNRUNNABLE = "model_unrunnable"


# Rows per fetch when a fill service STREAMS the sheet (binary,
# matched to the write batch so a scan and the insert it feeds move in
# the same size steps). Admission reads the sheet lazily so a SCOPED
# fill stops once it has its N rows, rather than materializing every
# eligible row to take the first few.
FILL_SCAN_CHUNK = 1000
# Rows per write when a fill service touches many at once (binary):
# admission materializes a fill's queue, cancel abandons what is left
# of it, list delete purges.
FILL_WRITE_BATCH = 1000


# Stable codes for a fill that DIED, distinct from the admission
# refusals above: those answer a request that never started, these
# ride Fill.error_code and reach the client as the failed
# fill's two-tier error. MODEL_UNRUNNABLE is deliberately the SAME
# member the admission lane refuses under: an address that cannot run
# is one fact, whether it is caught at the provider or at claim time.
class FillFailureCode(StrEnum):
    FILL_UNRUNNABLE = "fill_unrunnable"
    SOURCE_GONE = "source_gone"
    MODEL_UNRUNNABLE = FillErrorCode.MODEL_UNRUNNABLE
    PROVIDER_THROTTLED = "provider_throttled"
    SEARCH_THROTTLED = "search_throttled"


class FillTaskStatus(StrEnum):
    """A queue entry's lifecycle, and DELIBERATELY disjoint from
    StoredCellState: this says whether the WORK finished, never what came of
    it. A task that exhausts its attempts is DONE, and the giving-up is
    diagnosed on its cells.

    A leased QUEUED task is the running one: the lease is what makes it
    recoverable when a worker dies, so a separate running state would
    only need un-setting on crash.

    ABANDONED is the durable record of consent granted and NOT spent.
    Cancel writes it over the fill's unclaimed tasks in one statement,
    which is what lets a later resume ask what a stopped fill still
    owed instead of reconstructing it.

    ROW_MISSING is the one task outcome that has no cell to carry it:
    the row was gone when the task came up, so there is nothing to
    diagnose and nothing a resume could owe (unlike ABANDONED, which a
    resume re-targets). The fill goes on without it."""

    QUEUED = "queued"
    DONE = "done"
    ABANDONED = "abandoned"
    ROW_MISSING = "row_missing"


class FillStatus(StrEnum):
    """A fill's lifecycle. Terminal states are terminal: recovery
    is a NEW fill (refill), never a reopened row."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# The statuses a fill can still be claimed into or cancelled from:
# ONE definition, because "is this fill live" is asked by the queue,
# the admission gate, the cancel path, the derived-pending read, and
# the bench's account cap.
LIVE_FILL_STATUSES = (FillStatus.PENDING, FillStatus.RUNNING)


class StoredCellState(StrEnum):
    """What a fill made of one cell. FILLED plus the blank causes;
    every member is terminal for the run that wrote it.

    FILLED IS STORED, deliberately. Absence of a record beside a value
    does identify a filled cell, but it makes the count a SEQ SCAN of
    the whole sheet, which stays O(rows) no matter how few cells the
    column ever touched. A record makes the same count an indexed
    lookup bounded by cells actually filled, and the per-column poll
    needs it every four seconds.

    So ABSENCE means exactly one thing: never attempted.

    PENDING is still not here. A queued FillTask on a live fill IS
    pending, which is what keeps admission from writing to the sheet
    at all and what leaves nothing to sweep when a fill stops. The
    wire's WireCellState carries `pending` (the client needs it to
    shimmer) and omits FILLED (a value plus no state is enough for a
    renderer); storage is the mirror of that, because the two are
    answering different questions."""

    FILLED = "filled"
    NO_EVIDENCE = "no_evidence"
    # Budget exhaustion: the model spent its request/tool budget
    # without producing an answer. SETTLED (refill never re-targets
    # it; the same config re-buys the same refusal), unlike
    # MODEL_ERROR, which is infrastructure and re-runs.
    NO_ANSWER = "no_answer"
    # An answer arrived but failed provenance verification (uncited,
    # a citation that resolved to nothing, or a contacts record that
    # never tied the person to the row's own identity): the SERP was
    # the best answer to the query, not the truth about this row.
    # SETTLED like NO_ANSWER (the same config re-buys the same
    # unconfirmable answer).
    UNVERIFIED = "unverified"
    UNPARSEABLE = "unparseable"
    TYPE_MISMATCH = "type_mismatch"
    MODEL_ERROR = "model_error"
    TRANSIENT = "transient"
    # A tool's provider did not serve this row. The SHEET keys on the BASE
    # status code only, never on a (tool, code) cross product: which
    # tool, and the tool's own code, ride the cell record's `tools`
    # map beside the state, so a tool can add a failure mode without
    # this vocabulary growing. NOT_CONFIGURED is written at once (no
    # retry changes it) and re-runs on Continue once set up.
    # UNAVAILABLE (rate limited, unreachable, or erroring) parks and
    # retries up to the attempt cap ONLY when the whole row blanked; a
    # row that answered its other columns lands it at once (a park
    # would hold hostage cells the user can already read), and a later
    # Continue re-targets it.
    TOOL_NOT_CONFIGURED = "tool_not_configured"
    TOOL_UNAVAILABLE = "tool_unavailable"


# The causes that PARK a row for retry instead of settling a cell (the
# worker's branch); every other cause is terminal for the run.
RETRY_CAUSES = (StoredCellState.TRANSIENT, StoredCellState.TOOL_UNAVAILABLE)
# The worker's idle heartbeat (binary): how long it sleeps when no fill
# has claimable work before scanning again.
FILL_WORKER_IDLE_SECONDS = 4
# A parked task's backoff base (binary): multiplied by the attempt so
# a provider under pressure is asked less often each time, and long enough
# that a retry never lands inside the same rate window it just hit.
FILL_RETRY_BACKOFF_SECONDS = 32
# The supervising heartbeat (binary): while rows are IN FLIGHT the
# worker stamps the fill this often, so a healthy-but-slow fill (rows
# parked on slow searches) never reads as an unreporting worker
# (terminal rows also stamp; this covers the gaps between them).
FILL_HEARTBEAT_REFRESH_SECONDS = 64

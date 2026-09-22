"""Bounds + enums for the lists domain."""

from __future__ import annotations

from enum import StrEnum

from jobs.constants import JOB_LOOP_IDLE_SECONDS, JobFailureCode
from openbower_kernel.ranks import RANK_MAX_LENGTH as KERNEL_RANK_MAX_LENGTH
from openbower_schema.fills import (
    FREE_SEARCH_FILL_BUDGET as FREE_SEARCH_FILL_BUDGET,
)
from openbower_schema.fills import (
    NODE_RUN_ATTEMPTS as NODE_RUN_ATTEMPTS,
)
from openbower_schema.fills import (
    ROW_LEASE_STALE_SECONDS as ROW_LEASE_STALE_SECONDS,
)
from openbower_schema.lists import CELL_MAX_LENGTH as WIRE_CELL_MAX_LENGTH
from openbower_schema.lists import (
    COLUMN_KEY_GRAMMAR as COLUMN_KEY_GRAMMAR,
)
from openbower_schema.lists import (
    COLUMN_KEY_MAX_LENGTH as COLUMN_KEY_MAX_LENGTH,
)
from openbower_schema.lists import (
    COLUMN_LABEL_MAX_LENGTH as COLUMN_LABEL_MAX_LENGTH,
)
from openbower_schema.webhooks import DEFAULT_WEBHOOK_CADENCE_SECONDS as DEFAULT_WEBHOOK_CADENCE_SECONDS
from openbower_schema.webhooks import WEBHOOK_CADENCE_SECONDS as WEBHOOK_CADENCE_SECONDS

LABEL_MAX_LENGTH = 120
# Rows per list: sized to hold a full confident lead list.
MAX_LIST_ROWS = 50_000
# Per-cell character ceiling, deliberately above the incumbent
# spreadsheets (Sheets 50,000, Excel 32,767): our sheet is not bounded
# by theirs, at the honest cost that a maxed cell truncates if an
# exported CSV lands back in those tools.
CELL_MAX_LENGTH = WIRE_CELL_MAX_LENGTH
MAX_ROWS_PER_ADD = 1000
# A rank's bounds (openbower_kernel.ranks owns the column bound),
# shared by every model that carries one, a row, a node, a run: appends
# keep it at four characters for the largest sheet, and moves into the
# same gap add about a character per six. Past the rebalance length a
# sheet is re-spaced by the `rerank` job and a path in place, either
# well short of the bound.
RANK_MAX_LENGTH = KERNEL_RANK_MAX_LENGTH
RANK_REBALANCE_LENGTH = 32
# A re-space waits for the list's open fills (a walk's cursor holds a
# key of the old spacing); how long a waiting rerank sleeps between looks.
RERANK_WAIT_SECONDS = 60
# Idempotency key a webhook caller may supply on an ingest push (else one
# is minted). Opaque to us: any scheme the caller dedupes on (a ULID, a
# UUID, their own event id), bounded so it can key a store cheaply.
MAX_INGEST_EVENT_ID_LENGTH = 255
# How many push problems one ingest 400 lists before truncating: enough
# to fix a batch in one round, bounded so a wholly-malformed push cannot
# return a response as large as itself.
MAX_INGEST_PROBLEMS = 20
DEFAULT_ROWS_PAGE = 50
MAX_ROWS_PAGE = 200
DEFAULT_INDEX_PAGE = 50
MAX_INDEX_PAGE = 200
MAX_LIST_COLUMNS = 70
# Column keys that would shadow a literal route under `columns/` (the
# AI and webhook add collections): refused at every key claim.
RESERVED_COLUMN_KEYS: frozenset[str] = frozenset({"ai", "webhook"})
# Bounds the unpaged folder GET (the whole taxonomy ships at once).
MAX_FOLDERS = 200
# CSV uploads: a whole-CRM export fits comfortably; anything bigger is
# probably not a lead list.
MAX_CSV_BYTES = 5 * 1024 * 1024
# Multipart framing headroom over the file itself (the Content-Length
# pre-check fires before the body is parsed).
IMPORT_BODY_OVERHEAD = 16 * 1024
# A node kind's name: the Node.kind column bound AND the registry's name
# guard, one number so a registered name always fits the column.
NODE_KIND_MAX_LENGTH = 32
# A node's identity, the kind-declared projection of its config that the
# unique key indexes (binary): an agent id is 26, a future kind may
# project a hash.
NODE_IDENTITY_MAX_LENGTH = 64

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
# Autofill run ids the provisioner collects and publishes in ONE flush
# per pass (binary): a PUBLISH-batch size, not consumer concurrency. The
# provisioner only puts ids on the bus and the consumers pull at their
# own rate (Kafka buffers between them), so this sizes how much a pass
# amortizes the broker round-trip over, not how many run at once. Matches
# the manual lane's per-fill depth.
AUTOFILL_PUBLISH_BATCH = 1000
# Manual node runs the provisioner publishes per fill PER PASS (binary):
# offering every live fill the same per-pass batch is the fairness point,
# so a wide fill cannot flood the bus ahead of a smaller one beside it in
# a pass. It bounds a PASS, not the standing QUEUED depth (the consumers
# drain at their own rate; Kafka buffers between).
FILL_PUBLISH_BATCH = 1000
# Live fills per ACCOUNT (binary). A preview run is not a fill and never
# counts: that lane is bounded at one live run per account by
# supersede (the preview must always answer).
MAX_ACTIVE_FILLS = 4

NODE_RUN_STATUS_MAX_LENGTH = 16
CELL_STATE_MAX_LENGTH = 32
# The claimant's identity stamp (hostname:pid); diagnostic, bounded.
LEASED_BY_MAX_LENGTH = 128


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
    # A Send webhook column waits on this column's path; the user edits
    # or deletes those columns first (409).
    COLUMN_WAITED_ON = "column_waited_on"
    COLUMNS_FULL = "columns_full"
    PROVIDER_RETIRED = "provider_retired"
    MODEL_UNRUNNABLE = "model_unrunnable"


# The preview's refusals, its own lane (a preview run is a NodeRun, never
# a fill): the machine leg of the {error, detail} envelope
# POST /v1/runs/preview answers with. The codes keep the "test" word
# because that is the button the user pressed.
class PreviewErrorCode(StrEnum):
    # The generic leg a refusal carries until a subclass names its own.
    TEST_REFUSED = "test_refused"
    # A hand-fed test row past the wire's preview bounds: too many
    # values, or a key or value over its length. Refused, never
    # truncated.
    TEST_ROW_INVALID = "test_row_invalid"
    # The drafted config cannot run at all (no model, an unknown tool):
    # refused before a run exists, the same gate fill admission runs.
    MODEL_UNRUNNABLE = FillErrorCode.MODEL_UNRUNNABLE


# The ingest webhook's wire error codes, its own lane (separate from the
# column/fill admission refusals above): the machine leg of the
# {error, detail} envelope the POST answers with, classified by the
# producer. INVALID is a 400 (a value or key the push must fix);
# UNAVAILABLE is a 503 (the bus could not accept it; retry).
class IngestErrorCode(StrEnum):
    INGEST_INVALID = "ingest_invalid"
    INGEST_UNAVAILABLE = "ingest_unavailable"


# The Send webhook column's refusals, all 400: each names something in
# the request the caller changes (a key, a row, a destination), never a
# race worth waiting out. Body references answer with a code and copy
# rather than a 404, so the drawer can act on them.
class WebhookColumnErrorCode(StrEnum):
    # The generic leg a refusal carries until a subclass names its own.
    WEBHOOK_COLUMN_REFUSED = "webhook_column_refused"
    COLUMN_UNKNOWN = "column_unknown"
    COLUMN_NOT_AI = "column_not_ai"
    ROW_UNKNOWN = "row_unknown"
    DESTINATION_UNKNOWN = "destination_unknown"
    COLUMN_NOT_WEBHOOK = "column_not_webhook"
    COLUMN_NOT_DATA = "column_not_data"


class WebhookCellWord(StrEnum):
    """What a Send webhook cell says for a row, off its newest run: the
    server side of the wire's WebhookCellState (parity pinned)."""

    WAITING = "waiting"
    SENT = "sent"
    FAILED = "failed"


class WebhookRunOutcome(StrEnum):
    """What a webhook run's stored result says about its delivery. SENT
    and FAILED are what a DONE run holds; RETRYING rides a run parked
    after a transient failure, so the last attempt's delivery and
    error stay readable while the run waits for its next window."""

    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


# Rows per fetch when a fill service STREAMS the sheet (binary,
# matched to the write batch so a scan and the insert it feeds move in
# the same size steps). Admission reads the sheet lazily so a SCOPED
# fill stops once it has its N rows, rather than materializing every
# eligible row to take the first few.
FILL_SCAN_CHUNK = 1000
# Rows per write when a service touches many at once (binary): a
# walk's slice queues a page's runs, cancel abandons what is left
# of it, list delete purges, the cron sweep pages its deletes, and a
# re-space rewrites a sheet's ranks under its lock.
FILL_WRITE_BATCH = 1000
# Rows per digest: what one flush tick claims for one webhook node
# (binary). A node with more due rows sends the rest on later ticks,
# each digest its own delivery.
WEBHOOK_FLUSH_BATCH = 256


# Stable codes for a fill that DIED, distinct from the admission
# refusals above: those answer a request that never started, these
# ride the fill job's error_code and reach the client as the failed
# fill's two-tier error. MODEL_UNRUNNABLE is deliberately the SAME
# member the admission lane refuses under: an address that cannot run
# is one fact, whether it is caught at the provider or at claim time.
class FillFailureCode(StrEnum):
    FILL_UNRUNNABLE = "fill_unrunnable"
    MODEL_UNRUNNABLE = FillErrorCode.MODEL_UNRUNNABLE
    # Deliberately the SAME members admission refuses under: the agent
    # gone, or its provider retired, is one fact whether it is caught at
    # the click or by the first run of a fill already walking.
    AGENT_MISSING = FillErrorCode.COLUMN_AGENT_MISSING
    PROVIDER_RETIRED = FillErrorCode.PROVIDER_RETIRED


# The copy those two facts carry, at the click and mid-fill alike: the
# user's next step, never internal vocabulary.
AGENT_MISSING_MESSAGE = "The agent this column used has been deleted. Write a new prompt to fill it again."
PROVIDER_RETIRED_MESSAGE = "This agent's provider is no longer supported; open the agent and pick a current model."
# What a fill says when the RUNNER failed it (a slice that kept raising,
# a tick that kept dying): the stored cause stays on the job for the
# operator, the user reads a next step.
JOB_FAILURE_COPY: dict[str, str] = {
    JobFailureCode.CRASHED: "The fill stopped unexpectedly. Fill remaining finishes what it left.",
    JobFailureCode.EXHAUSTED: "The fill's worker stopped responding. Fill remaining finishes what it left.",
}


class NodeRunStatus(StrEnum):
    """A queue entry's lifecycle, and DELIBERATELY disjoint from
    StoredCellState: this says whether the WORK finished, never what came of
    it. A task that exhausts its attempts is DONE, and the giving-up is
    diagnosed on its cells.

    PROCESSING is the running one: a consumer claimed it. There is no
    lease to renew, because run_cell is time-bounded, so a task
    PROCESSING past the worst-case run means a DEAD consumer, and the
    reclaim scan (reclaim_stale_processing) returns it to READY off
    last_state_change_at (to DEFERRED for a deferred kind, which the
    worker never runs).

    DEFERRED is the run whose processing is put off to a later time AND
    to its node kind's own processor: not skipped, not ready, not
    queued for the shared worker. `not_before` holds the time; the kind
    (a webhook run, claimed by the flush in a batch at its window) says
    who claims it. The agent lanes pick READY and claim READY|QUEUED, so
    a DEFERRED run is invisible to them by status alone.

    ABANDONED is the durable record of consent granted and NOT spent.
    Cancel writes it over the fill's unclaimed tasks in one statement,
    which is what lets a later resume ask what a stopped fill still
    owed instead of reconstructing it.

    ROW_MISSING is the one task outcome that has no cell to carry it:
    the row was gone when the task came up, so there is nothing to
    diagnose and nothing a resume could owe (unlike ABANDONED, which a
    resume re-targets). The fill goes on without it.

    LIST_MISSING is ROW_MISSING's coarser sibling for the automatic
    path: the whole list was gone when the task came up (deleted after
    the row was pushed), so the task settles terminally with nothing to
    diagnose. A fill-backed task never sees it (its fill job was swept
    with the list); it is the autofill worker's way to retire an orphaned
    task instead of a delete-cascade off the list."""

    # The non-terminal lifecycle, in order: READY (admitted, eligible,
    # not yet handed to the transport), QUEUED (handed off / published,
    # not re-provisioned), PROCESSING (a consumer owns it and is
    # running), DEFERRED (owed, but to a later time and another
    # processor). All four are open; the terminals below are not.
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    DEFERRED = "deferred"
    DONE = "done"
    ABANDONED = "abandoned"
    ROW_MISSING = "row_missing"
    LIST_MISSING = "list_missing"


# The states a task still owes work in: it shimmers on the sheet, the
# reclaim scan watches it, a fill is complete only when it has none, and
# the automatic lane admits ONE run per (row, node) in them. The
# terminals are everything else; keeping the NON-terminal set explicit
# is what the reclaim scan's partial index, the open-run key, and the
# pending derivation key on.
NON_TERMINAL_NODE_RUN_STATES = (
    NodeRunStatus.READY,
    NodeRunStatus.QUEUED,
    NodeRunStatus.PROCESSING,
    NodeRunStatus.DEFERRED,
)


# Preview runs (a NodeRun that owns its input, the agent builder's
# one-row diagnostic) are throwaway: the compose cron's
# prune_preview_runs command deletes them past this age. A day, the
# baseline: generous next to any live poll (staleness reads in
# seconds), so a prune can never race a run anyone is watching.
PREVIEW_RUN_MAX_AGE_SECONDS = 86_400


# How long a targeted fill job parks between looks at its runs: the
# jobs loop's own cadence, so a fill reads complete within seconds of
# its last run settling and a retuned loop retunes this with it.
FILL_POLL_SECONDS = JOB_LOOP_IDLE_SECONDS


CELL_SOURCE_MAX_LENGTH = 8


class CellSource(StrEnum):
    """Who wrote a cell's state: an AGENT (a fill's run or an automatic
    one; which fill, if any, is the record's fill_run_id) or a person
    (MANUAL, once the grid can be edited; that writer does not exist
    yet). Completion is source-agnostic: a filled cell is done whoever
    filled it."""

    AGENT = "agent"
    MANUAL = "manual"


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

    PENDING is still not here. A queued NodeRun on a live fill IS
    pending, which is what keeps admission from writing to the sheet
    at all and what leaves nothing to sweep when a fill stops. The
    wire's WireCellState carries `pending` (the client needs it to
    shimmer) and omits FILLED (a value plus no state is enough for a
    renderer); storage is the mirror of that, because the two are
    answering different questions."""

    FILLED = "filled"
    NO_EVIDENCE = "no_evidence"
    # Budget exhaustion: the model spent its request/tool budget
    # without producing an answer. SETTLED: the model's own verdict, a
    # quiet word on the sheet (every blank re-runs on the next fill), unlike
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
# consumer's branch); every other cause is terminal for the run.
RETRY_CAUSES = (StoredCellState.TRANSIENT, StoredCellState.TOOL_UNAVAILABLE)
# The manual fill provisioner's idle heartbeat (binary): how long it
# sleeps when no live fill has a READY task before scanning again.
FILL_PROVISION_IDLE_SECONDS = 4
# The autofill provisioner's idle heartbeat (binary): how long it sleeps
# when the automatic queue is empty before scanning again.
AUTOFILL_WORKER_IDLE_SECONDS = 4
# A parked task's backoff base (binary): multiplied by the attempt so
# a provider under pressure is asked less often each time, and long enough
# that a retry never lands inside the same rate window it just hit.
FILL_RETRY_BACKOFF_SECONDS = 32

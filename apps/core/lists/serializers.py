"""Request validation + wire builders for /v1/lists and /v1/runs.
Wire dicts mirror
the schema package's models one-to-one (the web types against those)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rest_framework import serializers

from agents.serializers import AgentConfigRequest
from jobs.models import Job
from openbower_schema.agents import PROMPT_MAX_LENGTH
from openbower_schema.fills import CellRunResult, FillError
from openbower_schema.fills import FillRunWire as WireFillRun
from openbower_schema.lists import AiColumn, CellStateWire, IngestColumn, WebhookCellState, WebhookColumn
from openbower_schema.lists import FolderSummary as WireFolderSummary
from openbower_schema.lists import IngestSchema as WireIngestSchema
from openbower_schema.lists import ListRowWire as WireListRow
from openbower_schema.lists import ListSummary as WireListSummary
from openbower_schema.runs import NodeRunWire as WireNodeRun

from .constants import (
    COLUMN_KEY_GRAMMAR,
    COLUMN_KEY_MAX_LENGTH,
    COLUMN_LABEL_MAX_LENGTH,
    DEFAULT_WEBHOOK_CADENCE_SECONDS,
    LABEL_MAX_LENGTH,
    MAX_INGEST_EVENT_ID_LENGTH,
    MAX_INGEST_PROBLEMS,
    MAX_LIST_COLUMNS,
    MAX_LIST_ROWS,
    MAX_ROWS_PER_ADD,
    ColumnType,
)
from .models import Folder, List, ListRow, NodeRun

if TYPE_CHECKING:
    from .services.fills import FillProgress


class ColumnDef(serializers.Serializer):
    key = serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH)
    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[t.value for t in ColumnType])


class ListCreateRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)
    columns = ColumnDef(many=True, required=False, max_length=MAX_LIST_COLUMNS, default=list)

    def validate_columns(self, value: list[dict]) -> list[dict]:
        keys = [c["key"] for c in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("column keys must be unique")
        return value


class ListPatchRequest(serializers.Serializer):
    """Rename and/or move; at least one field must be present."""

    label = serializers.CharField(max_length=LABEL_MAX_LENGTH, required=False)
    folder_id = serializers.CharField(max_length=26, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("nothing to change")
        return attrs


class RowsAddRequest(serializers.Serializer):
    """Manual append. Cell values are strings, enforced at the boundary: one
    non-string cell would fail the wire schema on every later read,
    bricking the sheet with no repair path."""

    # No per-cell max_length: an oversize cell is authored input and
    # CLAMPS in ListService.add_rows (rejecting would fail a whole
    # batch over one long value).
    rows = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField(allow_blank=True, trim_whitespace=False)),
        min_length=1,
        max_length=MAX_ROWS_PER_ADD,
    )


class IngestRequest(serializers.Serializer):
    """A webhook push: rows plus an OPTIONAL caller-supplied idempotency key.
    Absent -> the endpoint mints a ULID; present -> used verbatim (a blank
    key is a client bug, rejected). No per-cell max_length on the field:
    the per-cell clamp is not the serializer's job, it rides the shared
    cells_for_storage the POST runs before publishing (so the bus carries
    the clamped, stored form, not a raw oversized cell) and add_rows runs
    again on append. The whole-message byte bound sized to the bus message
    limit is a separate, durable-backend decision (see
    ingest.get_ingest_publisher)."""

    rows = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField(allow_blank=True, trim_whitespace=False)),
        min_length=1,
        max_length=MAX_ROWS_PER_ADD,
    )
    # Taken VERBATIM (trim_whitespace=False): stripping would mutate a key
    # the caller dedupes on.
    event_id = serializers.CharField(
        required=False, allow_blank=False, max_length=MAX_INGEST_EVENT_ID_LENGTH, trim_whitespace=False
    )


class FolderRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)


class AiColumnRequest(serializers.Serializer):
    """POST /v1/lists/{id}/columns/ai: the quick tab sends `config`
    (validated through the agents app's shared serializer, never
    retyped), the other tab sends `agent_id`; exactly one of the two.
    No column label rides the request: the OUTPUTS are the columns
    (each output's key and label name what its cells land under).
    `confirmed_row_count` echoes the count the user consented to: the
    fill's range, so rows appended after the click are never walked."""

    config = AgentConfigRequest(required=False)
    agent_id = serializers.CharField(required=False, allow_blank=True, default="", max_length=26)
    confirmed_row_count = serializers.IntegerField(min_value=0)
    # Scope: fill only the FIRST N eligible rows (0 = all, the absent
    # default; a sent value must be positive).
    rows = serializers.IntegerField(required=False, default=0, min_value=1, max_value=MAX_LIST_ROWS)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if (attrs.get("config") is not None) == bool(attrs.get("agent_id")):
            raise serializers.ValidationError("exactly one of config or agent_id is required")
        return attrs


class BenchRunRequest(serializers.Serializer):
    """POST /v1/runs/bench: a drafted config plus ONE inline row, the
    bench's hand-fed values. SHAPE only: the bench bounds are refused
    by the service (never truncated), so the refusal rides the
    {error, detail} envelope instead of DRF's field shape, which the
    client cannot read."""

    config = AgentConfigRequest()
    # trim_whitespace=False: the bench's whole value is fidelity to
    # what a fill would run, so a hand-fed value must reach the model
    # exactly as typed, never silently stripped.
    row = serializers.DictField(child=serializers.CharField(allow_blank=True, trim_whitespace=False))


class ColumnRefillRequest(serializers.Serializer):
    """POST /v1/lists/{id}/columns/{key}/refill: the column names
    everything except the optional scope, so the body carries at most
    `rows` (first N eligible unanswered rows; absent = all)."""

    # Continue's leg: bound the new fill to THIS stopped fill's own
    # unresolved rows (resume, never widen).
    resume_fill = serializers.CharField(required=False, allow_blank=True, default="", max_length=26)

    rows = serializers.IntegerField(required=False, default=0, min_value=1, max_value=MAX_LIST_ROWS)

    # The consent echo, as the admit lane has. OPTIONAL because resume
    # spends what a previous consent already bought and the widening
    # gestures are the ones that need a number in front of them; 0
    # means the caller showed no count and is not echoing one.
    confirmed_row_count = serializers.IntegerField(required=False, default=0, min_value=0, max_value=MAX_LIST_ROWS)


class ColumnRenameRequest(serializers.Serializer):
    """PATCH /v1/lists/{id}/columns/{key}: the label, and only the
    label. The key is the path, not a field, because it never moves."""

    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)


class ColumnOrderRequest(serializers.Serializer):
    """PATCH /v1/lists/{id}/column-order: the full ordered key list.

    The WHOLE order, not a move instruction, because the server has to
    check the set is unchanged and a move (from, to) cannot be checked
    against anything: it would apply to whatever the sheet happens to
    hold now, which is the stale-client case this endpoint refuses."""

    keys = serializers.ListField(
        # The SHAPE a column key can have, the same expression
        # ColumnDef pins: a key outside it names no column that could
        # ever exist, so it is a malformed request and not a sheet that
        # moved. Without it such a key reaches the stale check and gets
        # told to try again, which can never work.
        child=serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH),
        min_length=1,
        max_length=MAX_LIST_COLUMNS,
    )


class ColumnPromptRequest(serializers.Serializer):
    """PATCH /v1/lists/{id}/columns/{key}/prompt: the one editable fact
    of a column's fill agent, bounded by the contract's own prompt cap
    (the same bound the agent serializers enforce)."""

    prompt = serializers.CharField(max_length=PROMPT_MAX_LENGTH)


class ColumnAddRequest(serializers.Serializer):
    """POST /v1/lists/{id}/columns: one BLANK column. The key is not a
    field; it derives server-side from the label, by the one derivation
    rule fills also use, so an output whose label reads the same lands
    on this key and refuses rather than writing here."""

    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)
    type = serializers.ChoiceField(choices=[t.value for t in ColumnType])


# Wire builders CONSTRUCT the contract models (never hand-assembled
# dicts): a field the contract gained but these forgot, or a wrong
# type, fails loudly here instead of drifting to the client's zod.


def list_wire(target: List) -> dict[str, Any]:
    return WireListSummary(
        id=str(target.id),
        label=target.label,
        folder_id=target.folder_id,
        columns=target.columns,
        origin=target.origin,
        origin_ref=target.origin_ref,
        row_count=target.row_count,
        created_at=target.created_at.isoformat(),
        updated_at=target.updated_at.isoformat(),
    ).model_dump()


def ingest_schema_wire(target: List) -> dict[str, Any]:
    """The push schema: every column keyed by what a producer sends, with
    the AI columns marked autopopulated. A producer provides the hard
    columns and may leave the AI ones blank for autofill, or send a value
    to pin its own (write-if-blank keeps it)."""
    columns = [
        IngestColumn(key=c.key, label=c.label, type=c.type, autopopulated=isinstance(c, AiColumn))
        for c in target.columns
        # A webhook column holds no data: a producer never sends into it.
        if not isinstance(c, WebhookColumn)
    ]
    return WireIngestSchema(columns=columns).model_dump()


def validate_ingest_rows(target: List, rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
    """Validate a push against the sheet's columns BEFORE it is accepted,
    so a malformed push is refused up front (a 400 the producer can fix)
    rather than stored as sent. Returns (problems, storable_rows): each
    problem is one line (row index plus cause, an unknown key or a value
    the column's type refuses), and when problems is EMPTY the storable
    rows are what to publish. They run through the SAME cells_for_storage
    the writers use (normalize to the type's canonical form, then clamp),
    so the bus carries exactly what add_rows will store on append (a pushed
    "1,234" is published and stored as "1234", a 70k-char cell clamped once
    here, not at full size on the bus). This is the push's reaction to a
    shape mismatch: an unknown key or a type mismatch both become a 400. A
    blank value is 'not provided', kept as-is (a producer may leave an AI
    column for autofill). Capped at MAX_INGEST_PROBLEMS."""
    from .services.lists import cells_for_storage

    types = {column.key: column.type for column in target.columns if not isinstance(column, WebhookColumn)}
    problems: list[str] = []
    storable_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        storable, mismatches = cells_for_storage(types, row, where="ingest")
        storable_rows.append(storable)
        problems.extend(f"row {index}: unknown column {key!r}" for key in row if key not in types)
        problems.extend(f"row {index}: {mismatch}" for mismatch in mismatches)
        if len(problems) >= MAX_INGEST_PROBLEMS:
            return problems[:MAX_INGEST_PROBLEMS], storable_rows
    return problems, storable_rows


def folder_wire(folder: Folder, *, list_count: int) -> dict[str, Any]:
    # list_count is server truth: the web's folder-delete consent copy
    # counts on it, and loaded pages may not cover the folder.
    return WireFolderSummary(
        id=str(folder.id),
        label=folder.label,
        list_count=list_count,
        created_at=folder.created_at.isoformat(),
        updated_at=folder.updated_at.isoformat(),
    ).model_dump()


def node_run_wire(run: NodeRun, result: CellRunResult | None) -> dict[str, Any]:
    """One run by id (GET /v1/runs/{id}, and the bench's POST and cancel
    echoes): its status, its stored result once it finished with one,
    and its latest state change for the client's staleness read."""
    return WireNodeRun(
        id=str(run.id),
        status=run.status,
        result=result,
        heartbeat_at=(run.last_state_change_at or run.created_at).isoformat(),
        created_at=run.created_at.isoformat(),
    ).model_dump()


def row_wire(
    row: ListRow,
    states: dict[str, CellStateWire] | None = None,
    webhooks: dict[str, WebhookCellState] | None = None,
) -> dict[str, Any]:
    """A sheet row with its AI cell states and its webhook cell words
    beside its values. ONE shape rather than paged reads walking in
    lockstep, which was a client-side join carried over the network."""
    return WireListRow(
        id=str(row.id), position=row.position, data=row.data, states=states or {}, webhooks=webhooks or {}
    ).model_dump()


def _fill_run_wire(fill: Job, progress: FillProgress) -> dict[str, Any]:
    # The fill is a JOB of kind fill: its consent is the payload, its
    # walk's cursor the progress, its lifecycle the job's. The wire's
    # five words derive from the job's status plus whether a run has
    # been claimed. Two-tier error: both legs travel together or not at
    # all (a code with no copy would leave the client nothing to render
    # verbatim), gated on the DOCUMENTED predicate exactly as the column
    # summary gates last_error: one fact, one rule, on every wire.
    # Counters and the heartbeat DERIVE from the task rows and cell
    # states at read time (passed in, so a page of runs pays ONE grouped
    # read, not per run); nothing writes them onto the job.
    from .jobs.fill import FillJob
    from .services import fill_progress

    consent = FillJob.model_validate(fill.payload)
    cursor = FillJob.Progress.model_validate(fill.progress)
    status = fill_progress.status_of(fill, started=progress.started)
    error = FillError(code=fill.error_code, message=fill.error) if status == "failed" and fill.error_code else None
    return WireFillRun(
        id=str(fill.id),
        list_id=fill.target_id,
        agent_id=consent.agent_id,
        status=status,
        column_keys=consent.column_keys,
        counters=progress.counters,
        confirmed_row_count=cursor.targeted if cursor.targeted_at else consent.consented,
        targeted_at=cursor.targeted_at.isoformat() if cursor.targeted_at else None,
        # The base model's attribution field is the wire's started_by;
        # authorization stays account membership.
        started_by=fill.user_id,
        heartbeat_at=progress.heartbeat.isoformat() if progress.heartbeat else None,
        error=error,
        created_at=fill.created_at.isoformat(),
        updated_at=fill.updated_at.isoformat(),
    ).model_dump()


def fill_run_wire(fill: Job) -> dict[str, Any]:
    """ONE run's wire, for the echo paths (admit/cancel/refill) that
    return a single fill. The fills PAGE uses fill_runs_wire, which reads
    all runs' progress in a fixed number of queries."""
    from .services.fill_progress import started
    from .services.fills import FillProgress, derive_counters, derive_heartbeat

    fill_run_id = str(fill.id)
    progress = FillProgress(derive_counters(fill_run_id), derive_heartbeat(fill_run_id), started(fill_run_id))
    return _fill_run_wire(fill, progress)


def fill_runs_wire(fills: list[Job]) -> list[dict[str, Any]]:
    """A PAGE of runs' wires, paying a FIXED number of grouped reads for
    all of them (not derive_counters + derive_heartbeat per run, which is
    a 3+4N walk on the four-second poll)."""
    from .services.fills import page_progress

    progress = page_progress([str(fill.id) for fill in fills])
    return [_fill_run_wire(fill, progress[str(fill.id)]) for fill in fills]


class WebhookColumnTestRequest(serializers.Serializer):
    """POST /v1/lists/{id}/columns/webhook/test: the destination, the
    columns the future column waits on, the columns it sends, the sample
    row, and the caller's values for the payload columns (the sample is
    editable). Cell values carry no length bound here: the service runs
    them through the one cell transform, so what is sent is what the
    sheet would hold."""

    destination_id = serializers.CharField(max_length=26)
    wait_keys = serializers.ListField(
        child=serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH),
        min_length=1,
        max_length=MAX_LIST_COLUMNS,
    )
    payload_keys = serializers.ListField(
        child=serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH),
        min_length=1,
        max_length=MAX_LIST_COLUMNS,
    )
    row_id = serializers.CharField(max_length=26)
    cells = serializers.DictField(child=serializers.CharField(allow_blank=True, trim_whitespace=False))
    # The column, once it exists: the digest is then scoped to its node.
    key = serializers.RegexField(
        COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH, required=False, allow_blank=True, default=""
    )

    def validate_wait_keys(self, value: list[str]) -> list[str]:
        return _unique_keys(value)

    def validate_payload_keys(self, value: list[str]) -> list[str]:
        return _unique_keys(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # The sample is exactly the payload columns: the drawer always
        # sends every one, so a mismatch is a malformed request, not a
        # choice to fall back on.
        if set(attrs["cells"]) != set(attrs["payload_keys"]):
            raise serializers.ValidationError("cells must carry exactly the payload keys")
        return attrs


class _WebhookColumnConfigFields(serializers.Serializer):
    """What a webhook column waits on, where it sends, what rides, and
    how often (one of the presets): the fields the add and the PATCH
    share. Each adds only what it can honour."""

    destination_id = serializers.CharField(max_length=26)
    wait_keys = serializers.ListField(
        child=serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH),
        min_length=1,
        max_length=MAX_LIST_COLUMNS,
    )
    payload_keys = serializers.ListField(
        child=serializers.RegexField(COLUMN_KEY_GRAMMAR, max_length=COLUMN_KEY_MAX_LENGTH),
        min_length=1,
        max_length=MAX_LIST_COLUMNS,
    )
    # Any positive number of seconds: the presets are the drawer's offer,
    # not the wire's law.
    interval_seconds = serializers.IntegerField(min_value=1, default=DEFAULT_WEBHOOK_CADENCE_SECONDS)

    def validate_wait_keys(self, value: list[str]) -> list[str]:
        return _unique_keys(value)

    def validate_payload_keys(self, value: list[str]) -> list[str]:
        return _unique_keys(value)


class WebhookColumnPatchRequest(_WebhookColumnConfigFields):
    """PATCH /v1/lists/{id}/columns/{key}/webhook: the whole config,
    rewritten, plus whether it runs."""

    enabled = serializers.BooleanField(default=True)


class WebhookColumnAddRequest(_WebhookColumnConfigFields):
    """POST /v1/lists/{id}/columns/webhook: the config plus the label the
    column's key derives from. A new column always runs, so `enabled` is
    not a field here: a request cannot send what the add would ignore."""

    label = serializers.CharField(max_length=COLUMN_LABEL_MAX_LENGTH)


def _unique_keys(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise serializers.ValidationError("column keys must be unique")
    return value

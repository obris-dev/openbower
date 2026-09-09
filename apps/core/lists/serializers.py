"""Request validation + wire builders for /v1/lists and /v1/fills.
Wire dicts mirror
the schema package's models one-to-one (the web types against those)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from agents.serializers import AgentConfigRequest
from openbower_kernel.provider_config import MAX_FILL_CONCURRENCY
from openbower_schema.agents import PROMPT_MAX_LENGTH
from openbower_schema.fills import CellRunResult, FillCounters, FillError
from openbower_schema.fills import FillRunDetail as WireFillRunDetail
from openbower_schema.fills import FillRunWire as WireFillRun
from openbower_schema.lists import CellStateWire
from openbower_schema.lists import FolderSummary as WireFolderSummary
from openbower_schema.lists import ListRowWire as WireListRow
from openbower_schema.lists import ListSummary as WireListSummary

from .constants import (
    COLUMN_KEY_MAX_LENGTH,
    COLUMN_LABEL_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    MAX_INGEST_EVENT_ID_LENGTH,
    MAX_LIST_COLUMNS,
    MAX_LIST_ROWS,
    MAX_ROWS_PER_ADD,
    ColumnType,
    FillStatus,
)
from .models import Fill, Folder, List, ListRow


class ColumnDef(serializers.Serializer):
    key = serializers.RegexField(r"^[a-z0-9_]+$", max_length=COLUMN_KEY_MAX_LENGTH)
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
    key is a client bug, rejected). A flat serializer, NOT a subclass of
    RowsAddRequest: the async path never runs RowsAddRequest's oversize-cell
    clamp (that lives in ListService.add_rows), so it must not inherit the
    'no per-cell cap because add_rows clamps' tradeoff. The byte bound sized
    to the bus message limit is a durable-backend decision (see
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
    `confirmed_row_count` echoes the count the user consented to
    (admission 409s on drift)."""

    config = AgentConfigRequest(required=False)
    agent_id = serializers.CharField(required=False, allow_blank=True, default="", max_length=26)
    confirmed_row_count = serializers.IntegerField(min_value=0)
    # The optional DOWNWARD-only concurrency override; 0 means unset.
    concurrency = serializers.IntegerField(required=False, default=0, min_value=0, max_value=MAX_FILL_CONCURRENCY)
    # Scope: fill only the FIRST N eligible rows (0 = all, the absent
    # default; a sent value must be positive).
    rows = serializers.IntegerField(required=False, default=0, min_value=1, max_value=MAX_LIST_ROWS)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if (attrs.get("config") is not None) == bool(attrs.get("agent_id")):
            raise serializers.ValidationError("exactly one of config or agent_id is required")
        return attrs


class TestFillRequest(serializers.Serializer):
    """POST /v1/fills/test: a drafted config plus ONE inline row, the
    bench's hand-fed values. SHAPE only: the bench bounds are refused
    by the admission (never truncated), so the refusal rides the
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
        child=serializers.RegexField(r"^[a-z0-9_]+$", max_length=COLUMN_KEY_MAX_LENGTH),
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


def fill_run_detail_wire(fill: Fill, result: CellRunResult | None) -> dict[str, Any]:
    """The single-run read (GET /v1/fills/{id}): the poll envelope's
    fields plus kind and, for a COMPLETE test run, its stored result.
    Reads the poll builder so the two projections cannot drift."""
    return WireFillRunDetail(**fill_run_wire(fill), kind=fill.kind, result=result).model_dump()


def row_wire(row: ListRow, states: dict[str, CellStateWire] | None = None) -> dict[str, Any]:
    """A sheet row with its AI cell states beside its values. ONE
    shape rather than two paged reads walking in lockstep, which was a
    client-side join carried over the network."""
    return WireListRow(id=str(row.id), position=row.position, data=row.data, states=states or {}).model_dump()


def fill_run_wire(fill: Fill) -> dict[str, Any]:
    # Two-tier error: both legs travel together or not at all (a code
    # with no copy would leave the client nothing to render verbatim),
    # gated on the DOCUMENTED predicate exactly as the column summary
    # gates last_error: one fact, one rule, on every wire.
    error = (
        FillError(code=fill.error_code, message=fill.error_message)
        if fill.status == FillStatus.FAILED and fill.error_code
        else None
    )
    return WireFillRun(
        id=str(fill.id),
        list_id=fill.list_id,
        agent_id=fill.agent_id,
        status=fill.status,
        column_keys=fill.column_keys or [],
        # Every declared counter, projected by NAME off the model's own
        # columns. Enumerating them here by hand is how a counter ships
        # as a zero while the worker writes it.
        counters=FillCounters(**{name: getattr(fill, name, 0) for name in FillCounters.model_fields}),
        confirmed_row_count=fill.confirmed_row_count,
        # The base model's attribution field is the wire's started_by;
        # authorization stays account membership.
        started_by=fill.user_id,
        heartbeat_at=fill.heartbeat_at.isoformat() if fill.heartbeat_at else None,
        error=error,
        created_at=fill.created_at.isoformat(),
        updated_at=fill.updated_at.isoformat(),
    ).model_dump()

"""Lists wire shapes shared by the backend and the web client.

A list is a content-agnostic SHEET: columns (a typed display schema) and
rows (data keyed by column keys). Nothing here references companies;
company behavior interprets a chosen column's values at use time.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

# A CLOSED union on purpose: codegen emits a zod enum, so an unknown
# type is a parse error, never a silently unstyled column. Types drive
# RENDERING only.
ColumnType = Literal["text", "number", "currency", "date", "url", "email"]

ListOrigin = Literal["discover", "csv", "manual"]

# Wire bounds on the contract (binary). The agents domain DERIVES its
# output bounds from these: an output becomes a column when a fill
# maps it onto a sheet, and a wider bound there would truncate
# persisted data at the seam.
COLUMN_KEY_MAX_LENGTH = 40
COLUMN_LABEL_MAX_LENGTH = 80


def derive_column_key(label: str, *, key: str = "") -> str:
    """THE label-to-key derivation, for every path that puts a column
    on a sheet: a CSV header, a blank column add, an agent output a
    fill maps down. It lives on the CONTRACT because a fill decides
    what an output key MEANS by matching it against the sheet's keys,
    so a second derivation anywhere does not produce a second key, it
    changes which column an output is judged against. What that match
    then does is FillAdmissionService._resolve_columns's rule, stated
    there and nowhere else. The web mirrors this one function
    (_components/agent-config/lib/output-key.ts)."""
    return re.sub(r"[^a-z0-9]+", "_", (key or label).lower()).strip("_")[:COLUMN_KEY_MAX_LENGTH]


class ColumnFill(BaseModel):
    """A column's fill linkage: present exactly on AI columns (the
    agent that fills it; the ephemeral-vs-roster custody rides the
    agent, not the column)."""

    agent_id: str
    current_fill_id: str = Field(
        default="",
        description="The fill that speaks for this column, stored here when it opens. "
        "Blank on a column filled before it was recorded. Clients read it off "
        "ColumnFillSummary, which the fills poll serves; it is declared here because "
        "this model is what the column's own structure is, and an undeclared key is "
        "dropped on every list read.",
    )


class ListColumn(BaseModel):
    key: str = Field(max_length=COLUMN_KEY_MAX_LENGTH, description="Stable snake_case key; row data dicts key on it.")
    label: str = Field(
        max_length=COLUMN_LABEL_MAX_LENGTH, description="Display label, as the user (or the CSV header) wrote it."
    )
    type: ColumnType = Field(description="Sheet display type; drives rendering only.")
    fill: ColumnFill | None = Field(default=None, description="Present exactly on AI columns.")


class FolderSummary(BaseModel):
    """A flat, account-scoped bucket for lists (taxonomy, not behavior)."""

    id: str
    label: str
    list_count: int = Field(description="Server-side count; consent copy must not trust loaded pages.")
    created_at: str
    updated_at: str


class FoldersList(BaseModel):
    items: list[FolderSummary]


class ListSummary(BaseModel):
    """One list as the index shows it (and the save-list response)."""

    id: str
    label: str
    folder_id: str = Field(default="", description="The containing folder; empty = loose at the root.")
    columns: list[ListColumn] = Field(default=[], description="Display order.")
    origin: ListOrigin = Field(description="How the list came to exist.")
    origin_ref: str = Field(default="", description="e.g. the source run id for discover snapshots.")
    row_count: int = Field(description="Total rows (the sheet may page far beyond one response).")
    created_at: str
    updated_at: str


# The per-cell state the rows page ships. `pending` is the ONE
# non-terminal value (it is the queue state, and drives the shimmer);
# the rest are terminal. `filled` travels ONLY when the run that
# filled the cell had a degraded tool (the `tools` map beside it says
# which), so the value can carry its mark; a value with no entry at
# all IS filled and clean. Not-attempted is likewise the ABSENCE of
# any entry (rows past the fill's cutoff, or no fill at all), never an
# enum value.
WireCellState = Literal[
    "pending",
    "filled",
    "no_evidence",
    # The model spent its request/tool budget without producing an
    # answer: SETTLED (the same config re-buys the same refusal), unlike
    # model_error, which is infrastructure and retries.
    "no_answer",
    # An answer arrived but failed provenance verification (its
    # citations never confirmed it for THIS row): SETTLED, since the
    # same config re-buys the same unconfirmable answer.
    "unverified",
    "unparseable",
    "type_mismatch",
    "model_error",
    "transient",
    # A tool's door did not serve this row. The SHEET keys on the base
    # code only (which tool, and the tool's own code, ride `tools`):
    # not configured is written at once and re-runs on Continue once
    # set up; unavailable (rate limited, unreachable, or erroring past
    # the row's retries) parks first and lands after the attempt cap.
    "tool_not_configured",
    "tool_unavailable",
]


class CellStateWire(BaseModel):
    """One AI cell's state and the tool statuses of the run that wrote
    it: `tools` is tool -> status code (a base ToolStatus code or the
    tool's own; "open" for a tool that served), empty for a pending
    cell or a run before tools reported statuses. The client resolves
    copy by (tool, code) and tolerates a code it has not heard of."""

    state: WireCellState
    # A literal default, not default_factory: only the literal reaches
    # the JSON schema, so the generated client parses an entry without
    # the key instead of refusing it.
    tools: dict[str, str] = {}


class ListRowWire(BaseModel):
    id: str
    position: int = Field(description="1-based dense display/paging order.")
    data: dict[str, str] = Field(default={}, description="Cell values keyed by column key.")
    states: dict[str, CellStateWire] = Field(
        default={},
        description="AI cell states keyed by column key: every cell without a value, plus "
        "filled cells whose run had a degraded tool. Slim on absences by contract, so a "
        "long-filled sheet carries almost nothing here. A value in `data` with no entry here "
        "IS filled and clean, and never-attempted is likewise an absence.",
    )


class ListRowsPage(BaseModel):
    items: list[ListRowWire]
    next_cursor: str | None = Field(default=None, description="The last position when more rows exist.")


class ListsPage(BaseModel):
    items: list[ListSummary]
    next_cursor: str | None = Field(default=None, description="The last id when more lists exist.")


class RowsAdded(BaseModel):
    """The manual-append receipt."""

    added: int
    row_count: int


class ImportResult(BaseModel):
    """What a CSV upload produced."""

    list: ListSummary
    rows: int = Field(description="Rows imported.")
    skipped: int = Field(description="Blank lines and rows wider than the header, not imported.")

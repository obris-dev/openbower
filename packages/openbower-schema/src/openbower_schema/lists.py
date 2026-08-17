"""Lists wire shapes shared by the backend and the web client.

A list is a content-agnostic SHEET: columns (a typed display schema) and
rows (data keyed by column keys). Nothing here references companies;
company behavior interprets a chosen column's values at use time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# A CLOSED union on purpose: codegen emits a zod enum, so an unknown
# type is a parse error, never a silently unstyled column. Types drive
# RENDERING only.
ColumnType = Literal["text", "number", "currency", "date", "url", "email"]

ListOrigin = Literal["discover", "csv", "manual"]


class ListColumn(BaseModel):
    key: str = Field(description="Stable snake_case key; row data dicts key on it.")
    label: str = Field(description="Display label, as the user (or the CSV header) wrote it.")
    type: ColumnType = Field(description="Sheet display type; drives rendering only.")


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


class ListRowWire(BaseModel):
    id: str
    position: int = Field(description="1-based dense display/paging order.")
    data: dict[str, str] = Field(default={}, description="Cell values keyed by column key.")


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

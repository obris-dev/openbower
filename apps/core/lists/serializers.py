"""Request validation + wire builders for /v1/lists. Wire dicts mirror
the schema package's models one-to-one (the web types against those)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from openbower_schema.lists import FolderSummary as WireFolderSummary
from openbower_schema.lists import ListRowWire as WireListRow
from openbower_schema.lists import ListSummary as WireListSummary

from .constants import (
    COLUMN_KEY_MAX_LENGTH,
    COLUMN_LABEL_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    MAX_LIST_COLUMNS,
    MAX_ROWS_PER_ADD,
    ColumnType,
)
from .models import Folder, List, ListRow


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
    """Manual append. Cell values are strings, enforced at the door: one
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


class FolderRequest(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX_LENGTH)


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


def row_wire(row: ListRow) -> dict[str, Any]:
    return WireListRow(id=str(row.id), position=row.position, data=row.data).model_dump()

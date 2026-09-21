"""Cursor-pagination helpers shared by `/v1` list endpoints.

Lists order by `-id` (ULIDs are time-sortable) and page with
`?after=<last id>`, or by a richer key the endpoint names (a sheet's
rows page by rank and id behind an opaque cursor); either way a full
page implies a `next_cursor`, a short page means the end. `parse_limit` bounds `?limit=` without letting a caller
error a view with garbage.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework.exceptions import ValidationError
from rest_framework.request import Request


def parse_limit(request: Request, *, default: int, maximum: int) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return default
    # `isdecimal`, not `isdigit`: the latter admits Unicode digits (e.g. "²")
    # that `int()` rejects. The length bound guards the OTHER int() failure:
    # CPython raises above ~4300 digits, so bound BEFORE converting. Anything
    # longer than `maximum` is out of range anyway, so this loses nothing.
    # Both together keep garbage a clean 400, never an unhandled 500.
    if not raw.isdecimal() or len(raw) > len(str(maximum)) or int(raw) < 1:
        raise ValidationError("?limit= must be a positive integer")
    return min(int(raw), maximum)


def next_cursor_from(rows: list, *, limit: int, cursor: Callable[[Any], str] = lambda row: str(row.id)) -> str | None:
    """The `next_cursor` for a page: the last row's cursor when the page
    is full (there may be more), None otherwise. The cursor is the
    row's id unless the endpoint pages by something richer (a sheet's
    rows, by rank and id)."""
    return cursor(rows[-1]) if len(rows) == limit else None

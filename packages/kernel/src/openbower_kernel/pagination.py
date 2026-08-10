"""Cursor-pagination helpers shared by `/v1` list endpoints.

Lists order by `-id` (ULIDs are time-sortable) and page with
`?after=<last id>`; a full page implies a `next_cursor`, a short page
means the end. `parse_limit` bounds `?limit=` without letting a caller
error a view with garbage.
"""

from __future__ import annotations

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


def next_cursor_from(rows: list, *, limit: int) -> str | None:
    """The `next_cursor` for a page: the last row's id when the page is
    full (there may be more), None otherwise."""
    return str(rows[-1].id) if len(rows) == limit else None

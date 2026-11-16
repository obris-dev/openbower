"""How an endpoint answers a refused column write: one classifier for
every endpoint that writes a column (the sheet's column routes, a custom
signal's create), so a status added to the set reaches all of them and
none can drift from the others."""

from __future__ import annotations

from .constants import FillErrorCode
from .services.columns import ColumnRefused

# A 409 is a conflict with the sheet's state (retry after it changes);
# every other column refusal is a 400 (the request itself must change).
# Kept apart from the fill codes: the two sets are disjoint, and neither
# endpoint classifies by the other's.
COLUMN_CONFLICT_CODES = frozenset(
    {FillErrorCode.COLUMN_ORDER_STALE, FillErrorCode.COLUMN_WAITED_ON, FillErrorCode.COLUMN_IMMUTABLE}
)


def column_refusal_status(e: ColumnRefused) -> int:
    return 409 if e.code in COLUMN_CONFLICT_CODES else 400

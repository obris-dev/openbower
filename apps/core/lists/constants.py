"""Bounds + enums for the lists domain."""

from __future__ import annotations

from enum import StrEnum

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

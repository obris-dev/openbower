"""OpenBower shared schema.

Pydantic-first contract: every wire shape the web consumes is defined here
and codegen'd to zod for the web workspace, so both sides validate against
one definition.
"""

from .auth import AuthUser
from .discover import (
    Company,
    LookalikeGroup,
    LookalikeItem,
    LookalikeListResponse,
)
from .lists import (
    FoldersList,
    FolderSummary,
    ImportResult,
    ListColumn,
    ListRowsPage,
    ListRowWire,
    ListsPage,
    ListSummary,
    RowsAdded,
)

__all__ = [
    "AuthUser",
    "Company",
    "FolderSummary",
    "FoldersList",
    "ImportResult",
    "ListColumn",
    "ListRowWire",
    "ListRowsPage",
    "ListSummary",
    "ListsPage",
    "LookalikeGroup",
    "LookalikeItem",
    "LookalikeListResponse",
    "RowsAdded",
]

"""OpenBower shared schema.

Pydantic-first contract: every wire shape the web consumes is defined here
and codegen'd to zod for the web workspace, so both sides validate against
one definition.
"""

from .agents import (
    AgentCatalog,
    AgentConfig,
    AgentListItem,
    AgentOutput,
    AgentsList,
    AgentSummary,
    AgentTools,
    CatalogModel,
)
from .auth import AuthUser
from .discover import (
    Company,
    LookalikeGroup,
    LookalikeItem,
    LookalikeListResponse,
)
from .fills import (
    CellRunResult,
    ColumnFillSummary,
    ColumnPromptWire,
    FillCounters,
    FillError,
    FillRunDetail,
    FillRunPage,
    FillRunWire,
)
from .lists import (
    ColumnFill,
    FoldersList,
    FolderSummary,
    ImportResult,
    IngestAccepted,
    ListColumn,
    ListRowsPage,
    ListRowWire,
    ListsPage,
    ListSummary,
    RowsAdded,
)
from .webhooks import (
    WebhookColumnTestResponse,
    WebhookDeliveriesPage,
    WebhookDeliveryWire,
    WebhookDestinationCreated,
    WebhookDestinationsList,
    WebhookDestinationWire,
    WebhookDigestData,
    WebhookDigestItem,
    WebhookEnvelope,
    WebhookPingData,
    WebhookSheetRef,
)

__all__ = [
    "AgentCatalog",
    "AgentConfig",
    "AgentListItem",
    "AgentOutput",
    "AgentSummary",
    "AgentTools",
    "AgentsList",
    "AuthUser",
    "CatalogModel",
    "CellRunResult",
    "ColumnFill",
    "ColumnFillSummary",
    "ColumnPromptWire",
    "Company",
    "FillCounters",
    "FillError",
    "FillRunDetail",
    "FillRunPage",
    "FillRunWire",
    "FolderSummary",
    "FoldersList",
    "ImportResult",
    "IngestAccepted",
    "ListColumn",
    "ListRowWire",
    "ListRowsPage",
    "ListSummary",
    "ListsPage",
    "LookalikeGroup",
    "LookalikeItem",
    "LookalikeListResponse",
    "RowsAdded",
    "WebhookColumnTestResponse",
    "WebhookDeliveriesPage",
    "WebhookDeliveryWire",
    "WebhookDestinationCreated",
    "WebhookDestinationWire",
    "WebhookDestinationsList",
    "WebhookDigestData",
    "WebhookDigestItem",
    "WebhookEnvelope",
    "WebhookPingData",
    "WebhookSheetRef",
]

"""Fill admission: the GATE. Everything that starts a fill goes
through this package's ONE-transaction method, and the columns write
that points the columns at the fill lands in the same transaction as
the fill and its queue, so no column ever names a fill that never
opened.

The AI column create (services/ai_columns.py) shares `base` and
`columns` with the fill, importing them from those modules; the
package exports the fill's service and the refusals both raise.

One room per concern: `errors` the vocabulary, `base` the model gate,
`columns` the column machinery and `targets` the metering cap (which
rows a fill targets is the agent processor's), `normal` the service
(fill_column). Account-scoped like every lists service."""

from .errors import (
    AccountFillsFull,
    ColumnAgentMissing,
    ColumnCollision,
    ColumnNoLongerFilled,
    ColumnsFull,
    DerivedKeyCollision,
    EmptyFill,
    FillColumnDownstream,
    FillColumnNotFound,
    FillRefused,
    FreeSearchBudget,
    ModelUnrunnable,
    NoEligibleRows,
    NothingToFill,
    ProviderRetiredRefusal,
    ReservedColumnKey,
    SameColumnFillActive,
)
from .normal import FillAdmissionService

__all__ = [
    "AccountFillsFull",
    "ColumnAgentMissing",
    "ColumnCollision",
    "ColumnNoLongerFilled",
    "ColumnsFull",
    "DerivedKeyCollision",
    "EmptyFill",
    "FillAdmissionService",
    "FillColumnDownstream",
    "FillColumnNotFound",
    "FillRefused",
    "FreeSearchBudget",
    "ModelUnrunnable",
    "NoEligibleRows",
    "NothingToFill",
    "ProviderRetiredRefusal",
    "ReservedColumnKey",
    "SameColumnFillActive",
]

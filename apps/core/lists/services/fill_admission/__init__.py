"""Fill admission: the GATE. Everything that creates a fill goes
through this package's ONE-transaction methods, and the columns write
lands in the same transaction as the fill and its queue: anything less
can append a column whose fill never lands, leaving the sheet carrying
a column nothing will ever fill.

One room per concern: `errors` the vocabulary, `base` the custody and
the model gate, `columns` the column machinery and `targets` the
metering cap (which rows a fill targets is the agent processor's),
`normal` the service (admit/refill). Account-scoped like every lists
service."""

from .errors import (
    AccountFillsFull,
    ColumnAgentMissing,
    ColumnCollision,
    ColumnNoLongerFilled,
    ColumnsFull,
    ColumnTypeChanged,
    DerivedKeyCollision,
    EmptyFill,
    FillColumnNotFound,
    FillRefused,
    FreeSearchBudget,
    ModelUnrunnable,
    NoEligibleRows,
    ProviderRetiredRefusal,
    RefillEmpty,
    ReservedColumnKey,
    ResumeConfigChanged,
    ResumeRunNotFound,
    SameColumnFillActive,
)
from .normal import FillAdmissionService

__all__ = [
    "AccountFillsFull",
    "ColumnAgentMissing",
    "ColumnCollision",
    "ColumnNoLongerFilled",
    "ColumnTypeChanged",
    "ColumnsFull",
    "DerivedKeyCollision",
    "EmptyFill",
    "FillAdmissionService",
    "FillColumnNotFound",
    "FillRefused",
    "FreeSearchBudget",
    "ModelUnrunnable",
    "NoEligibleRows",
    "ProviderRetiredRefusal",
    "RefillEmpty",
    "ReservedColumnKey",
    "ResumeConfigChanged",
    "ResumeRunNotFound",
    "SameColumnFillActive",
]

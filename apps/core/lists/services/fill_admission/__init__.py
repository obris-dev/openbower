"""Fill admission: the GATE. Everything that creates a fill goes
through this package's ONE-transaction methods, and for the normal
kind the columns write lands in the same transaction as the fill and
its queue: anything less can append a column whose fill never lands,
leaving the sheet carrying a column nothing will ever fill.

One room per concern, the kind boundary a file boundary:
`errors` the shared vocabulary, `base` strictly what both kinds
execute, `columns` and `targets` the normal kind's machinery,
`normal` (admit/refill) and `test` the two
services, each kind admitting through its own admit().
Account-scoped like every lists service."""

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
    RowCountChanged,
    SameColumnFillActive,
    TargetCountChanged,
    TestFillActive,
    TestRowInvalid,
)
from .normal import FillAdmissionService
from .test import TestFillAdmission

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
    "RowCountChanged",
    "SameColumnFillActive",
    "TargetCountChanged",
    "TestFillActive",
    "TestFillAdmission",
    "TestRowInvalid",
]

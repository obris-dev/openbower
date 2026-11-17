"""Per-type shape validators for the contract's column types: the
fence between a model's parsed answer and a typed column. They live
IN the contract package because what values fit a type IS the type's
meaning: the sheet's write path enforces these rules and the runtime's
prompt hints state them, and both must read ONE declaration or the
model is punished for a rule it was never told. A wrong-shape value
refuses with its why so the caller can diagnose it as a type mismatch,
distinct from unparseable (the user's next step differs: fix the
output's description versus fix the model). Refusal beats coercion
wherever a value reads two ways: storing one reading of an ambiguous
date, or of a time with no zone, is a guess. text, url, and email
columns pass through untouched here; URL integrity is grounding custody
at the runtime, not a shape rule."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime

from .sheets import ColumnType


class CellTypeMismatch(Exception):
    """A value whose shape does not fit its column's type. Carries the
    column `key` and a `why` naming the value, so every refusal is a
    diagnosis by construction."""

    def __init__(self, key: str, why: str) -> None:
        super().__init__(f"{key}: {why}" if key else why)
        self.key = key
        self.why = why


# Digits, optional comma thousands separators, one optional decimal
# point. Grouping must be exact: "1,23" is a European decimal comma as
# plausibly as a typo, and either reading would be a guess.
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")

# One leading mark only. Trailing marks and alphabetic codes ("100
# USD") refuse: normalizing them means deciding what they meant.
_CURRENCY_SYMBOLS = frozenset("$€£¥")

# Year-first with one separator style throughout, or any ISO 8601 date or
# date and time the standard parser reads (its day kept as written).
# Anything else is either ambiguous (03/04/2026 reads two ways) or not a
# date.
_DATE = re.compile(r"^(\d{4})([-/.])(\d{1,2})\2(\d{1,2})$")

# The stored form: UTC, to the second, fixed width, so stored values sort
# in time order as text.
_DATETIME_STORED = "%Y-%m-%dT%H:%M:%SZ"


def _number(value: str, key: str) -> str:
    text = value.strip()
    if not _NUMBER.match(text):
        raise CellTypeMismatch(key, f'"{value}" is not a number')
    return text.replace(",", "")


def _currency(value: str, key: str) -> str:
    text = value.strip()
    symbol = ""
    if text and text[0] in _CURRENCY_SYMBOLS:
        symbol, text = text[0], text[1:].lstrip()
    if not _NUMBER.match(text):
        raise CellTypeMismatch(key, f'"{value}" is not an amount (a number with at most one leading currency symbol)')
    return symbol + text.replace(",", "")


def _date(value: str, key: str) -> str:
    text = value.strip()
    match = _DATE.match(text)
    if not match:
        return _written_day(value, text, key)
    year, _, month, day = match.groups()
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError as e:
        raise CellTypeMismatch(key, f'"{value}" is not a calendar date') from e
    return parsed.isoformat()


def _datetime(value: str, key: str) -> str:
    """An ISO 8601 date and time with its UTC offset, as
    datetime.fromisoformat reads it, stored in UTC. The offset is
    required: a time with no zone names a different moment in every zone
    it is read in."""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise CellTypeMismatch(key, f'"{value}" is not an ISO 8601 date and time, like 2026-01-15T09:30:00Z') from e
    if parsed.tzinfo is None:
        if _reads_as_date(text):
            raise CellTypeMismatch(
                key,
                f'"{value}" is a date; this column holds a date and a time, so add a time and zone, '
                "like 2026-01-15T09:30:00Z (or use a date column)",
            )
        raise CellTypeMismatch(
            key, f'"{value}" has no time zone; write it with Z or an offset, like 2026-01-15T09:30:00Z'
        )
    # A moment within a day of the calendar's ends can fall outside it once
    # moved to UTC.
    try:
        moment = parsed.astimezone(UTC)
    except OverflowError as e:
        raise CellTypeMismatch(key, f'"{value}" is outside the years 0001 to 9999 in UTC') from e
    return moment.strftime(_DATETIME_STORED)


def _written_day(value: str, text: str, key: str) -> str:
    """The day an ISO 8601 value states, as written: a date and time keeps
    its own day, never moved to UTC, since the day is in the value and a
    conversion is what would shift it. Only the time is dropped, which a
    date column does not hold."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise CellTypeMismatch(key, f'"{value}" is not an unambiguous date; write it year-first as YYYY-MM-DD') from e
    day = parsed.date()
    return day.isoformat()


# A bare date is the datetime type's near miss: it has neither the time nor
# the zone a moment needs, so it is refused with the type it belongs in.
def _reads_as_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


_VALIDATORS = {
    "number": _number,
    "currency": _currency,
    "date": _date,
    "datetime": _datetime,
}


# What each SHAPE-RULED type must look like, phrased for the model.
# Beside the validators that refuse it, deliberately: a value we
# reject at write time has to be a shape we asked for, or the runtime
# is enforcing a rule it never stated and the blank is our fault. Only
# the ruled types appear here; inventing a hint for a pass-through
# type would state a rule nothing enforces.
_SHAPE_HINTS = {
    "number": "Write it as digits only (optional sign, optional decimal point), with no units or words.",
    "currency": (
        "Write it as an amount: digits with at most one leading currency symbol, and no trailing code like USD."
    ),
    "date": "Write it year-first as YYYY-MM-DD; any other order is ambiguous and will be discarded.",
    "datetime": (
        "Write it as an ISO 8601 date and time with its UTC offset, like 2026-01-15T09:30:00Z; "
        "a time with no offset is ambiguous and will be discarded."
    ),
}


def shape_hint(column_type: ColumnType | str) -> str:
    """The sentence telling a model what this column's type must look
    like, or "" for the types that carry no shape rule."""
    return _SHAPE_HINTS.get(column_type, "")


def validate_cell(column_type: ColumnType | str, value: str, *, key: str = "") -> str:
    """The normalized string to store, or CellTypeMismatch. Only number,
    currency, date, and datetime carry shape rules; every other type
    returns the value untouched. `key` rides any refusal so the diagnosis
    names the column."""
    validator = _VALIDATORS.get(column_type)
    if validator is None:
        return value
    return validator(value, key)


def normalize_row(types: Mapping[str, str], data: Mapping[str, str]) -> tuple[dict[str, str], list[CellTypeMismatch]]:
    """THE row-level shape application every write path funnels through,
    so a stored cell is its type's CANONICAL form (validate_cell's return)
    by construction and no writer can skip normalization. Given a
    column-key -> type map and a row's cells, returns the cells with each
    value normalized, plus the mismatches a caller reacts to PER ITS TIER
    (the push rejects with a 400, an authored import tolerates, an autofill
    answer flags) without re-deciding the shape rule. A blank value, or a
    key with no typed column, passes through untouched and raises no
    mismatch; a value its type refuses is returned RAW in the map AND
    recorded in `mismatches`, so the caller alone decides whether a refusal
    stores, rejects, or flags."""
    normalized: dict[str, str] = {}
    mismatches: list[CellTypeMismatch] = []
    for key, value in data.items():
        column_type = types.get(key, "")
        if not column_type or not value.strip():
            normalized[key] = value
            continue
        try:
            normalized[key] = validate_cell(column_type, value, key=key)
        except CellTypeMismatch as e:
            normalized[key] = value
            mismatches.append(e)
    return normalized, mismatches

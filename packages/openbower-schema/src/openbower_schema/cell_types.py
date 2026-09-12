"""Per-type shape validators for the contract's column types: the
fence between a model's parsed answer and a typed column. They live
IN the contract package because what values fit a type IS the type's
meaning: the sheet's write path enforces these rules and the runtime's
prompt hints state them, and both must read ONE declaration or the
model is punished for a rule it was never told. A wrong-shape value refuses
with its why so the caller can diagnose it as a type mismatch,
distinct from unparseable (the user's next step differs: fix the
output's description versus fix the model). Refusal beats coercion
wherever a value reads two ways: storing one reading of an ambiguous
date is a guess. text, url, and email columns pass through untouched
here; URL integrity is grounding custody at the runtime, not a shape
rule."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date

from .lists import ColumnType


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

# Year-first with one separator style throughout. Anything else is
# either ambiguous (03/04/2026 reads two ways) or not a date.
_DATE = re.compile(r"^(\d{4})([-/.])(\d{1,2})\2(\d{1,2})$")


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
        raise CellTypeMismatch(key, f'"{value}" is not an unambiguous date; write it year-first as YYYY-MM-DD')
    year, _, month, day = match.groups()
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError as e:
        raise CellTypeMismatch(key, f'"{value}" is not a calendar date') from e
    return parsed.isoformat()


_VALIDATORS = {
    "number": _number,
    "currency": _currency,
    "date": _date,
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
}


def shape_hint(column_type: ColumnType | str) -> str:
    """The sentence telling a model what this column's type must look
    like, or "" for the types that carry no shape rule."""
    return _SHAPE_HINTS.get(column_type, "")


def validate_cell(column_type: ColumnType | str, value: str, *, key: str = "") -> str:
    """The normalized string to store, or CellTypeMismatch. Only
    number, currency, and date carry shape rules; every other type
    returns the value untouched. `key` rides any refusal so the
    diagnosis names the column."""
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

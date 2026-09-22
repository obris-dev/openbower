import re
from datetime import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _
from ulid import ULID, StrictMonotonicPolicy, ULIDGenerator

# 26-char Crockford-Base32 (digits + uppercase letters, excluding I, L, O, U).
# Matches what `new_ulid()` produces and what ULIDField stores.
_ULID_RE = re.compile(r"^[0-9A-HJ-KM-NP-TV-Z]{26}$")

# ONE generator per process, MONOTONIC (python-ulid's strict policy:
# two ids minted in the same millisecond count up in their random part
# instead of drawing two random ones, under the generator's own lock),
# so id order is insertion order even inside a bulk insert. Every
# keyset-by-id read (a list index by -id, a consent range captured as
# an id) rests on that; a plain ULID only promises it across
# milliseconds. Across processes the order is the clock's, which is
# what "insertion order" can mean between two writers anyway.
_generator = ULIDGenerator(policy=StrictMonotonicPolicy())


def is_valid_ulid(value: str) -> bool:
    """True iff `value` matches the canonical ULID shape (26 chars,
    Crockford-Base32 alphabet).

    `fullmatch` (not `match`) so a trailing newline can't sneak through:
    in default re mode `$` matches before a final `\\n`, which would let
    `"ULID\\n"` pass as a valid id.
    """
    return bool(_ULID_RE.fullmatch(value))


def new_ulid() -> str:
    """A fresh id off the process's monotonic generator."""
    return str(_generator.generate())


def min_ulid_at(dt: datetime) -> str:
    """Smallest ULID whose timestamp is `dt` (ms-truncated).

    For id-range cutoffs equivalent to a `created_at` cutoff: ULIDs are
    lex-sortable and time-monotonic at ms resolution, so `id < min_ulid_at(dt)`
    selects exactly rows whose ULID-ms < dt-ms. A row whose ULID-ms equals
    dt-ms survives this cycle (random bits > all-zeros) and is caught next
    cycle: boundary-safe, never permanently missed.
    """
    milliseconds = int(dt.timestamp() * 1000)
    return str(ULID.from_bytes(milliseconds.to_bytes(6, "big") + bytes(10)))


class ULIDField(models.CharField):
    """26-char Crockford-Base32 ULID, auto-generated on save if blank."""

    description = _("ULID (Universally Unique Lexicographically Sortable Identifier)")

    def __init__(self, verbose_name: str | None = None, *, max_length: int = 26, **kwargs) -> None:
        # `**kwargs`-transparent (a base field should be): the fixed max_length
        # is asserted, everything else (default, db_column, validators, ...)
        # forwards to CharField, so a future model option deconstruct() re-emits
        # can't trap migration load with a TypeError.
        if max_length != 26:
            raise ValueError("ULIDField max_length is fixed at 26")
        kwargs.setdefault("editable", False)
        super().__init__(verbose_name=verbose_name, max_length=26, **kwargs)

    def pre_save(self, model_instance: models.Model, add: bool) -> str:
        value: str = getattr(model_instance, self.attname)
        if not value:
            value = new_ulid()
            setattr(model_instance, self.attname, value)
        return value

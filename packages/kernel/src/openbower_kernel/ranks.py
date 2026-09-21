"""Fractional ranks: a row's ORDER as a string that sorts, so a move is
one write on the moved row and never a renumbering of its neighbours.
Identity stays the id; a rank says only where a row sits.

WHY A STRING. Between any two integers there is eventually no third
one, so an integer order must renumber everything below an insert.
Between any two strings there is always a third (append a character),
so a row moved between two neighbours gets a key strictly between
their keys, and nothing else changes.

THE ALPHABET. 62 digits, `0-9` then `A-Z` then `a-z`, compared byte by
byte. Postgres must compare the same way, so a rank column carries the
C collation (a locale collation would put `a` before `B` and break
every comparison below).

A KEY HAS TWO PARTS.

1. An INTEGER part, whose first character says how many digits follow:
   `a` one (`a0` .. `az`, 62 keys), `b` two (`b00` .., 3,844 keys),
   `c` three, up to `z`. Counting UP through them is an append, which is
   why 50,000 appended rows never need more than four characters. `Z`
   one digit, `Y` two, .. `A` are the mirror image for counting DOWN,
   for a row moved above the first one.
2. An optional FRACTIONAL tail, used only when a key must land strictly
   between two existing keys: like decimals, the midpoint of `a1` and
   `a2` is `a1V` (V is the middle digit), of `a1` and `a1V` is `a1G`,
   and so on, about one character per six moves into the same gap. A
   tail never ends in `0`, so `a1` and `a10` can never both exist.

    first_key()              -> "a0"   the first key (nothing on either side)
    respace_keys(held)       -> fresh keys, one per member, disjoint from held
    key_between("a4", None)  -> "a5"   an append
    key_between(None, "a0")  -> "Zz"   a row moved to the very top
    key_between("a1", "a2")  -> "a1V"  a row moved between two others
    keys_between(a, b, n)    -> n keys in order, for a bulk append

Keys are only ever COMPARED, never decoded, so a sheet can be re-spaced
(fresh, evenly counted keys for the same order) and no reader notices;
that is what a rebalance does once repeated moves into one gap have made
a key long. The one key the scheme cannot go below, `A` plus 26 zeros,
is refused, so "a key below the lowest" always exists.

This module is the one place the codebase names the package, with
the vocabulary the sheet uses; every key in the database was made by
its scheme, so the scheme is stored data and cannot change under it."""

from __future__ import annotations

from collections.abc import Sequence

from fractional_indexing import BASE_62_DIGITS, FIError, generate_key_between, generate_n_keys_between
from fractional_indexing import validate_order_key as _validate

DIGITS = BASE_62_DIGITS
_ALPHABET = frozenset(DIGITS)
# The longest key a column stores. Appends keep a key at four characters
# for a sheet of tens of thousands of rows, and moves into the same gap
# add about a character of tail per six; a walker's cursor carries a
# key from outside, so the bound is checked before the arithmetic sees it.
RANK_MAX_LENGTH = 64


class RankError(ValueError):
    """A key the scheme did not make, or an order it cannot satisfy: the
    one error this module raises, whatever the arithmetic raised."""


def validate(key: str) -> None:
    """Refuse a key the scheme did not make: empty, over the bound, off
    the alphabet, a bad head, a short integer part, a fractional tail
    ending in the smallest digit, the floor."""
    # The arithmetic indexes the head before it checks anything, and
    # never checks the alphabet or the length; a key from outside is
    # screened here so it can only ever fail as a RankError.
    if not key or len(key) > RANK_MAX_LENGTH or not _ALPHABET.issuperset(key):
        raise RankError(f"not a rank: {key!r}")
    try:
        _validate(key, DIGITS)
    except FIError as e:
        raise RankError(str(e)) from e


def first_key() -> str:
    """The key for the first (or only) member of an order: what a fresh
    sheet's first row, a node alone on its path, or a bench run holds.
    Named so a writer says what it is placing rather than spelling the
    two absent neighbours."""
    return key_between(None, None)


def key_between(a: str | None, b: str | None) -> str:
    """A key strictly between `a` and `b`: None on either side means no
    bound (the top of the order, the bottom); `first_key()` names the
    case with nothing on either side."""
    try:
        return generate_key_between(a, b, DIGITS)
    except FIError as e:
        raise RankError(str(e)) from e


def respace_keys(held: Sequence[str]) -> list[str]:
    """Fresh keys for the members holding `held`, one each and in the
    order given, DISJOINT from every key in it: entirely below the
    lowest or entirely above the highest, whichever side gives the
    shorter keys. A re-space rewrites every member of an order under a
    unique (parent, rank) index, and that index is checked row by row
    as the UPDATE proceeds, so a fresh key a not-yet-visited member
    still holds would collide, in an order the database chooses; a
    disjoint set cannot, whatever the order and however the write is
    batched. The side nearer a0 gives the shorter keys, so re-spacing
    settles around a0 rather than climbing; either side has room for
    about 10^46 keys."""
    keys = list(held)
    below = keys_between(None, min(keys), len(keys))
    above = keys_between(max(keys), None, len(keys))
    longest_below = max(len(key) for key in below)
    longest_above = max(len(key) for key in above)
    # The side whose longest key is shorter; below on a tie.
    return below if longest_below <= longest_above else above


def keys_between(a: str | None, b: str | None, n: int) -> list[str]:
    """`n` keys in order strictly between `a` and `b`: an append of many
    rows (`b` None) counts up from `a`, so the keys stay as short as
    sequential appends make them."""
    try:
        return generate_n_keys_between(a, b, n, DIGITS)
    except FIError as e:
        raise RankError(str(e)) from e

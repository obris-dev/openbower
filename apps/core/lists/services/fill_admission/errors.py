"""The refusal taxonomy, shared by the AI column create and the
column fill: both raise from this one set, so the wire's error shapes
cannot fork."""

from __future__ import annotations

from ...constants import (
    AGENT_MISSING_MESSAGE,
    FREE_SEARCH_FILL_BUDGET,
    MAX_ACTIVE_FILLS,
    MAX_LIST_COLUMNS,
    PROVIDER_RETIRED_MESSAGE,
    FillErrorCode,
)


class FillRefused(Exception):
    """Base for admission refusals: `code` is the machine leg the view
    maps to a status, str(self) is server-authored copy the client
    renders verbatim (tier 1)."""

    code = FillErrorCode.FILL_REFUSED


class ColumnAgentMissing(FillRefused):
    """The agent this column ran on has been deleted. A deleted agent
    deliberately leaves its columns ORPHANED (the values stay, they
    just cannot be produced again), so this is a normal state and owes
    the user a next step rather than a stack of internal vocabulary."""

    code = FillErrorCode.COLUMN_AGENT_MISSING

    def __init__(self) -> None:
        super().__init__(AGENT_MISSING_MESSAGE)


class SameColumnFillActive(FillRefused):
    """One live fill per column: the gate that prevents duplicate
    SPEND (write-if-blank already prevents data damage)."""

    code = FillErrorCode.FILL_ACTIVE

    def __init__(self) -> None:
        super().__init__("A fill is already running on this column.")


class AccountFillsFull(FillRefused):
    code = FillErrorCode.FILLS_FULL

    def __init__(self) -> None:
        super().__init__(f"This account already has {MAX_ACTIVE_FILLS} fills running; wait for one to finish.")


class EmptyFill(FillRefused):
    """No fill that does nothing: a fill needs rows."""

    code = FillErrorCode.EMPTY_FILL

    def __init__(self) -> None:
        super().__init__("This sheet has no rows to fill; add rows first.")


class NoEligibleRows(FillRefused):
    """The no-fill-that-does-nothing rule for a sheet the prompt cannot
    act on: every referenced variable renders blank on every row, so
    each run would land a noise blank."""

    code = FillErrorCode.NO_ELIGIBLE_ROWS

    def __init__(self) -> None:
        super().__init__("No rows have values for this prompt's variables.")


class NothingToFill(FillRefused):
    """The no-fill-that-does-nothing rule: the sheet has rows, but every
    one of them was already tried in this column."""

    code = FillErrorCode.NOTHING_TO_FILL

    def __init__(self) -> None:
        super().__init__("Every row of this column has already been tried.")


class FreeSearchBudget(FillRefused):
    """The free vendor's admission bound, refusing BEFORE it spends
    and naming the metered door."""

    code = FillErrorCode.FREE_SEARCH_BUDGET

    def __init__(self, *, searches: int) -> None:
        super().__init__(
            f"This fill could need up to {searches:,} searches; free search is budgeted for "
            f"{FREE_SEARCH_FILL_BUDGET} per fill. Switch search to a metered vendor (a deployment setting) for"
            " metered search."
        )


class ColumnNoLongerFilled(FillRefused):
    """The column is on the sheet, but its agent no longer declares an
    output that lands there. An agent save refuses an output change
    while its columns exist, so only a save that raced the column's
    create reaches this. A REFUSAL, not a 404: the column is right there
    in front of the user."""

    code = FillErrorCode.FILL_COLUMN_RETIRED

    def __init__(self, *, key: str) -> None:
        self.key = key
        super().__init__(
            f"This agent no longer writes the {key} column. Delete this column; the agent's other columns still fill."
        )


class ColumnCollision(FillRefused):
    """An output's key names a column the sheet ALREADY HAS: refusal,
    never a silent suffix (the likely truth is accidental duplicate
    work, so the user decides).

    Existence is the whole test, not occupancy. Adopting an empty
    column meant asking whether any of its cells held a value, which
    is a scan of the sheet with no index behind it, run under the List
    lock while nothing else could add rows or import. A column is a
    thing the user can see and delete, so the cheap rule is also the
    legible one."""

    code = FillErrorCode.COLUMN_COLLISION

    def __init__(self, *, key: str, filled: bool = False) -> None:
        self.key = key
        # A column an agent ALREADY fills has a better next step than
        # deleting it: re-run it from the column itself. Telling that
        # user to delete a column of answers would be true and wrong.
        super().__init__(
            f"An agent already fills the {key} column; use Fill all remaining on it, or delete it to start over."
            if filled
            else f"This sheet already has a {key} column. Rename this output, or delete that column first."
        )


class DerivedKeyCollision(FillRefused):
    """Two of the agent's own outputs carry the SAME key: refusal,
    because the second output's answers would silently vanish into the
    first's column. The request serializer refuses duplicates at the
    provider; this guard covers configs that arrive any other way."""

    code = FillErrorCode.DERIVED_KEY_COLLISION

    def __init__(self, *, first: str, second: str) -> None:
        super().__init__(f"The {first} and {second} outputs would land in the same column; rename one.")


class ReservedColumnKey(FillRefused):
    code = FillErrorCode.RESERVED_KEY

    def __init__(self, *, label: str) -> None:
        super().__init__(f"{label!r} maps to a reserved column key; pick a different name.")


class ColumnsFull(FillRefused):
    code = FillErrorCode.COLUMNS_FULL

    def __init__(self) -> None:
        super().__init__(f"A sheet holds at most {MAX_LIST_COLUMNS} columns.")


class ProviderRetiredRefusal(FillRefused):
    """Acting on a substituted spec is a guess; the agent must be
    re-saved against a current provider first."""

    code = FillErrorCode.PROVIDER_RETIRED

    def __init__(self) -> None:
        super().__init__(PROVIDER_RETIRED_MESSAGE)


class ModelUnrunnable(FillRefused):
    code = FillErrorCode.MODEL_UNRUNNABLE

    def __init__(self, why: str) -> None:
        super().__init__(why)


class FillColumnDownstream(FillRefused):
    """The column is on the sheet and an agent fills it, but its node is
    not an entry action: it stands downstream of other work (a barrier),
    so a fill starting there would run rows the workflow has not brought
    to it. A REFUSAL, not a 404: the column is right there; the fill
    starts upstream."""

    code = FillErrorCode.FILL_COLUMN_DOWNSTREAM

    def __init__(self) -> None:
        super().__init__("This column fills after the columns it waits on. Fill those columns instead.")


class FillColumnNotFound(Exception):
    """No column with that key carries a fill on this list. Not a
    FillRefused: refusals answer an admissible ask, a missing column
    is 404 territory (like ListNotFound)."""

"""The refusal taxonomy, admission's shared vocabulary. Kind-agnostic
on purpose: both admission kinds raise from this one set, so the
wire's error shapes cannot fork by kind."""

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


class RefillEmpty(FillRefused):
    """The no-fill-that-does-nothing rule, worded for refill: the sheet
    has rows, but none of them is this column's remaining work."""

    code = FillErrorCode.REFILL_EMPTY

    def __init__(self) -> None:
        super().__init__("Every row of this column already has an answer.")


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


class ColumnTypeChanged(FillRefused):
    """An output this agent already fills now declares a DIFFERENT
    type from the column holding its answers.

    A refusal rather than a silent retype either way: retyping a
    column that holds answers makes every later answer TYPE_MISMATCH
    over data that cannot match, and refusing to retype strands the
    column at a type its own output never produces. A column's shape
    is fixed while it exists; changing it means deleting it, which is
    the same rule collisions follow."""

    code = FillErrorCode.COLUMN_TYPE_CHANGED

    def __init__(self, *, key: str, stored: str, wanted: str) -> None:
        self.key = key
        super().__init__(
            f"The {key} column is {stored} and this agent now writes {wanted}. "
            "Delete the column to change its type, or set the output back."
        )


class ColumnNoLongerFilled(FillRefused):
    """The column is on the sheet and carries a fill, but its agent no
    longer declares an output that lands there: an output renamed or
    removed since. A REFUSAL, not a 404, because the thing the user
    pointed at exists and they can see it; what changed is the ask."""

    code = FillErrorCode.FILL_COLUMN_RETIRED

    def __init__(self, *, key: str) -> None:
        self.key = key
        super().__init__(
            f"This agent no longer writes the {key} column; its outputs were renamed or removed. "
            "Open the agent to restore that output, or add a column for the new one."
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
            f"An agent already fills the {key} column; use Fill remaining on it, or delete it to start over."
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


class ResumeRunNotFound(FillRefused):
    """The named fill is not this SHEET's. Resolving it is what scopes
    the resume: NodeRun carries no account of its own (it is
    reached through its fill, which does), so reading rows for an
    unresolved id would query another account's table. Nothing crosses
    today, because row ids are ULIDs and the intersection empties, but
    that is the id scheme doing the scoping by accident. The refusal
    is also the honest answer: without it a foreign id reads back as
    "every row already has an answer", which is a false statement
    about the caller's own sheet."""

    code = FillErrorCode.RESUME_NOT_FOUND

    def __init__(self) -> None:
        super().__init__("That fill is not on this sheet; start a new fill instead.")


class ModelUnrunnable(FillRefused):
    code = FillErrorCode.MODEL_UNRUNNABLE

    def __init__(self, why: str) -> None:
        super().__init__(why)


class FillColumnNotFound(Exception):
    """No column with that key carries a fill on this list. Not a
    FillRefused: refusals answer an admissible ask, a missing column
    is 404 territory (like ListNotFound)."""

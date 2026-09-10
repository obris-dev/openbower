"""Which rows a NORMAL admission may consent to: the eligibility
predicate, the fresh-fill walk, the refill's remaining-work walk, and
the free provider's row cap. Normal-kind machinery throughout (a test
fill consents to exactly the one row it was handed), kept apart from
the sequences that consume it because every walk here is paged and
lazy, and that discipline is its own reading."""

from __future__ import annotations

from collections.abc import Iterator

from django.db import models

from agents.runtime.prompts import prompt_variables
from agents.tools.search.web_search import web_search_is_metered
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig
from openbower_schema.fills import SETTLED_CELL_STATES

from ...constants import (
    FILL_SCAN_CHUNK,
    FREE_SEARCH_FILL_BUDGET,
    MAX_LIST_ROWS,
    FillTaskStatus,
    StoredCellState,
)
from ...models import FillCellState, FillTask, List, ListRow


def row_is_eligible(data: dict, variables: set[str]) -> bool:
    """Whether the prompt can ACT on this row: at least one referenced
    variable renders non-blank. A prompt with no variables asks the
    same question everywhere, so every row qualifies. ONE definition
    for both targeting walks (the fresh walk and the refill walk)."""
    if not variables:
        return True
    return any(str(data.get(variable, "")).strip() for variable in variables)


def search_provider_is_free() -> bool:
    """Whether fill web searches run through a FREE vendor (which is
    what the budget bounds): the routing fact is the web-search
    wiring, read through the tool's own accessor, never the metered
    credentials (contact search runs its own metered roster
    regardless, and credentials alone route nothing)."""
    return not web_search_is_metered()


def free_provider_row_cap(config: AgentConfig) -> int:
    """How many rows this fill may consent to before the FREE search
    provider's budget refuses it. A CAP rather than a check on a total,
    because admission counts its rows as it walks them and never
    holds the whole set to measure it. Gated on WEB search alone:
    the budget bounds the free scraping vendor, and contact
    search is metered (its own roster carries no free vendor)
    whatever the wiring says, so a contacts-only fill spends nothing
    free. Unbounded when the vendor is metered or no free tool
    runs."""
    if config.searches_web and search_provider_is_free():
        return FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS
    return MAX_LIST_ROWS


def iter_eligible_rows(target_list: List, *, prompt: str) -> Iterator[tuple[str, int]]:
    """(row id, position) in SHEET ORDER for the rows the prompt can
    ACT on: at least one referenced variable renders non-blank. A
    row whose referenced variables are ALL blank would render an
    empty ask and land a noise blank, so it never enters a target
    set (and first-N means first N usable). A prompt with no
    variables asks the same question everywhere: every row is
    eligible.

    Paged by POSITION keyset in FILL_SCAN_CHUNK steps, the house
    rule, and here it is load-bearing rather than habit: a server
    side cursor left open by a caller that stops early (a scoped
    fill takes its N, a guard refuses mid-walk) cannot be recovered
    by the enclosing transaction's rollback. A keyset page holds
    nothing between chunks, so abandoning the walk costs nothing."""
    variables = prompt_variables(prompt)
    after = 0
    while True:
        chunk = list(
            ListRow.objects.filter(list_id=str(target_list.id), position__gt=after)
            .order_by("position")
            .only("id", "position", "data")[:FILL_SCAN_CHUNK]
        )
        if not chunk:
            return
        for row in chunk:
            if not variables or row_is_eligible(row.data, variables):
                yield str(row.id), row.position
        after = chunk[-1].position


class RefillTargets:
    """The column's remaining work, in sheet order, as a LAZY
    single-pass iterable, plus the one fact an empty stream cannot
    carry: whether anything was dropped because the prompt could not
    act on it.

    Owed-ness is judged across a SET of columns (one for a widening
    gesture, the resumed fill's whole set for a Continue), and a row is
    excluded only when EVERY column in it is done: settled, or holding
    a value (a user-entered or prior one; running it would spend a
    completion on a write-if-blank skip). A resume additionally drops
    rows outside that fill's consent. SETTLED means settled UNDER THAT
    CONFIG: a diagnosis the
    same config would just reproduce (no evidence found, budget spent
    without an answer, unparseable, wrong shape) holds only while
    `fingerprint` matches the one stamped on the cell at diagnosis
    time; a prompt edit changes the ask, so those rows re-target on the
    next refill. A FILLED cell settles UNCONDITIONALLY (its own
    disjunct: no fingerprint gate, no value test): the fill answered
    it once, and only deleting the column, which purges the record,
    buys it again. Infrastructure-tier cells (model_error, transient)
    and never-attempted rows always re-run.

    MEMORY IS BOUNDED BY ONE PAGE, and that is the point of the shape.
    The rows, the settled ids among them, and the owed ids among them
    are all fetched per page and dropped when the page is done, so
    nothing here grows with the sheet. Reading any of those three whole
    (which is what this replaced) costs a set of every row id on a
    50,000 row sheet, per set, per request.

    An OBJECT rather than a generator function because `dropped_any`
    has to reach the caller, and threading a mutable set down two call
    levels to collect it is an out-param wearing a different hat. It is
    only meaningful once the stream is exhausted: a caller that stops
    early has not asked the question of every row, which is exactly why
    the caller only reads it after a walk that consented to nothing."""

    def __init__(
        self,
        target_list: List,
        *,
        column_keys: list[str],
        fingerprint: str,
        prompt: str,
        owed_by: str = "",
    ) -> None:
        self.list_id = str(target_list.id)
        self.account_id = target_list.account_id
        # The columns owed-ness is judged across. A widening gesture
        # passes the ONE column the user clicked. A RESUME passes the
        # resumed fill's whole set, because a fill owns every output
        # its agent declares and finishing what it consented to cannot
        # mean finishing one column of it.
        self.column_keys = column_keys
        self.fingerprint = fingerprint
        self.prompt = prompt
        # The fill whose unspent consent bounds this walk (a resume), or
        # "" for the column's whole remainder.
        self.owed_by = owed_by
        self.dropped_any = False

    def __iter__(self) -> Iterator[tuple[str, int]]:
        variables = prompt_variables(self.prompt)
        after = 0
        while True:
            chunk = list(
                ListRow.objects.filter(list_id=self.list_id, position__gt=after)
                .order_by("position")
                .only("id", "position", "data")[:FILL_SCAN_CHUNK]
            )
            if not chunk:
                return
            after = chunk[-1].position
            ids = [str(row.id) for row in chunk]
            # Both membership sets are asked PER PAGE, against the ids
            # in hand. One extra indexed read per page buys a footprint
            # that does not move when the sheet grows.
            owed = self._owed_in(ids) if self.owed_by else None
            settled = self._settled_in(ids)
            for row in chunk:
                row_id = str(row.id)
                # The resume bound goes FIRST: a row this fill never
                # consented to is not its remaining work, so asking
                # anything else about it is wasted, and counting it as
                # dropped would blame the prompt for a row the scope
                # excluded.
                if owed is not None and row_id not in owed:
                    continue
                # Owed when ANY column still wants an answer: settled
                # by a diagnosis this config would reproduce, or
                # holding a value (a user's or a prior fill's, which
                # write-if-blank would refuse), settles that column
                # alone. A row is done only when every column is.
                done = settled.get(row_id, frozenset())
                if all(key in done or str(row.data.get(key, "") or "").strip() for key in self.column_keys):
                    continue
                if not row_is_eligible(row.data, variables):
                    # An owed row the prompt cannot act on. Recorded as
                    # a FLAG because an empty stream alone cannot say
                    # WHICH filter emptied it, and "the column is done"
                    # and "your prompt reads columns these rows have
                    # not got" need different next steps from the user.
                    self.dropped_any = True
                    continue
                yield row_id, row.position

    def _settled_in(self, ids: list[str]) -> dict[str, set[str]]:
        """For each of THESE rows, which of the walk's columns carry a
        diagnosis this config would only reproduce, so re-running one
        would re-buy the same refusal.

        Per COLUMN, not per row, because a run answers outputs
        independently: one column settling says nothing about its
        siblings. Filled cells are here too, but the non-blank test
        above already excludes them; this answers the other half, the
        blanks that are settled rather than retryable."""
        settled = models.Q(state=StoredCellState.FILLED) | models.Q(
            state__in=SETTLED_CELL_STATES, config_fingerprint=self.fingerprint
        )
        by_row: dict[str, set[str]] = {}
        for row_id, column_key in FillCellState.objects.filter(
            settled,
            account_id=self.account_id,
            list_id=self.list_id,
            column_key__in=self.column_keys,
            row_id__in=ids,
        ).values_list("row_id", "column_key"):
            by_row.setdefault(str(row_id), set()).add(column_key)
        return by_row

    def _owed_in(self, ids: list[str]) -> set[str]:
        """Which of THESE rows the resumed fill still owed. Its
        ABANDONED tasks are exactly the consent it was granted and did
        not spend, READ rather than reconstructed: stopping a fill
        records what it owed instead of erasing it, which is the reason
        the queue is materialized and survives its fill."""
        return {
            str(row_id)
            for row_id in FillTask.objects.filter(
                account_id=self.account_id,
                fill_run_id=self.owed_by,
                status=FillTaskStatus.ABANDONED,
                row_id__in=ids,
            ).values_list("row_id", flat=True)
        }

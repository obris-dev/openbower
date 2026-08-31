"""Fill admission: the GATE. Everything that creates a fill goes
through this service's ONE-transaction methods, and the columns write
lands in the same transaction as the fill and its queue: anything less
can append a column whose fill never lands, leaving the sheet carrying
a column nothing will ever fill. The List lock is taken LATE inside
that transaction, over the columns write and the bench seed alone;
admit()'s docstring carries the why.
admit() is the column add: caps and refusals, column resolution
(match-or-refuse), the config snapshot, the ephemeral-agent create,
the bench prewrite seed, the row-count echo, the fill row, and the bulk
insert of the queue. refill() is the one RECOVERY primitive,
admission-shaped: a NEW fill over the column's unanswered rows.
Account-scoped like every lists service."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from itertools import batched, islice

from django.conf import settings
from django.db import models, transaction

from agents.constants import TestRunStatus
from agents.models import Agent, AgentTestRun
from agents.providers import ModelUnavailable, model_for
from agents.runtime.answer import reserved_output_key
from agents.runtime.prompts import prompt_variables
from agents.services import AgentNotFound, AgentService, TestRunService, config_fingerprint
from openbower_schema.agents import LABEL_MAX_LENGTH as AGENT_LABEL_MAX_LENGTH
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig
from openbower_schema.fills import SETTLED_CELL_STATES, CellRunResult
from openbower_schema.lists import COLUMN_LABEL_MAX_LENGTH

from ..constants import (
    FILL_SCAN_CHUNK,
    FILL_WRITE_BATCH,
    FREE_SEARCH_FILL_BUDGET,
    LIVE_FILL_STATUSES,
    MAX_ACTIVE_FILLS,
    MAX_LIST_COLUMNS,
    MAX_LIST_ROWS,
    FillErrorCode,
    FillTaskStatus,
    StoredCellState,
)
from ..models import Fill, FillCellState, FillTask, List, ListRow
from .fill_progress import live_fill_count, try_finish
from .landing import land_row
from .lists import ListNotFound, ListService

logger = logging.getLogger(__name__)


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
        super().__init__("The agent this column used has been deleted. Write a new prompt to fill it again.")


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


class RowCountChanged(FillRefused):
    """The consent echo failed: the sheet GREW after the user read the
    numbers, so an unscoped fill would spend past the count the button
    named. Growth only (RULED, owner, 2026-08-27): the number is a
    spend CEILING, and a ceiling is violated only upward; a shrunken
    sheet fills fewer rows than consented, which betrays no one and
    refusing it was pure friction."""

    code = FillErrorCode.ROW_COUNT_CHANGED

    def __init__(self, actual: int) -> None:
        self.actual = actual
        super().__init__(
            f"The sheet has grown since you reviewed; it now has {actual} rows. Check the numbers and start again."
        )


class TargetCountChanged(FillRefused):
    """Refill's consent echo. Its own refusal, not the admit lane's,
    because the number is not the SHEET's size: refill counts what the
    column still owes, and reusing admit's copy told a 5,000 row sheet
    with two owed rows that it now has two rows.

    Same machine code, deliberately: the client's recovery for both is
    to re-read the count it showed and let the user start again."""

    code = FillErrorCode.ROW_COUNT_CHANGED

    def __init__(self, actual: int) -> None:
        self.actual = actual
        super().__init__(
            f"This column has {actual} rows left to fill, more than the number you reviewed. Check it and start again."
        )


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
    """The free door's admission bound, refusing BEFORE it spends and
    naming the paid door."""

    code = FillErrorCode.FREE_SEARCH_BUDGET

    def __init__(self, *, searches: int) -> None:
        super().__init__(
            f"This fill could need up to {searches:,} searches; free search is budgeted for "
            f"{FREE_SEARCH_FILL_BUDGET} per fill. Connect DataForSEO for metered search."
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
    door; this guard covers configs that arrive any other way."""

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
        super().__init__("This agent's provider is no longer supported; open the agent and pick a current model.")


class ResumeJobNotFound(FillRefused):
    """The named fill is not this SHEET's. Resolving it is what scopes
    the resume: FillTask carries no account of its own (it is
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


class ResumeConfigChanged(FillRefused):
    """Continue means finish THAT fill's consented work, and the config
    it consented under is part of the consent: resuming it under a
    different prompt would be a different fill wearing its name. The
    widening gestures run the new config."""

    code = FillErrorCode.CONFIG_CHANGED

    def __init__(self) -> None:
        super().__init__(
            "The prompt changed since this fill stopped. Use Fill next rows or Fill all remaining "
            "to run it with the new prompt."
        )


class ModelUnrunnable(FillRefused):
    code = FillErrorCode.MODEL_UNRUNNABLE

    def __init__(self, why: str) -> None:
        super().__init__(why)


class FillColumnNotFound(Exception):
    """No column with that key carries a fill on this list. Not a
    FillRefused: refusals answer an admissible ask, a missing column
    is 404 territory (like ListNotFound)."""


def _row_is_eligible(data: dict, variables: set[str]) -> bool:
    """Whether the prompt can ACT on this row: at least one referenced
    variable renders non-blank. A prompt with no variables asks the
    same question everywhere, so every row qualifies. ONE definition,
    used by admission's targeting and by the per-column summary."""
    if not variables:
        return True
    return any(str(data.get(variable, "")).strip() for variable in variables)


def _fill_search_door_is_free() -> bool:
    """Fills PREFER the paid door whenever its credentials exist; only
    a credential-less deploy runs fill searches through the free
    scraping door (which is what the budget bounds)."""
    return not (settings.DATAFORSEO_LOGIN and settings.DATAFORSEO_PASSWORD)


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
        target: List,
        *,
        column_keys: list[str],
        fingerprint: str,
        prompt: str,
        owed_by: str = "",
    ) -> None:
        self.list_id = str(target.id)
        self.account_id = target.account_id
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
                if not _row_is_eligible(row.data, variables):
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
                fill_id=self.owed_by,
                status=FillTaskStatus.ABANDONED,
                row_id__in=ids,
            ).values_list("row_id", flat=True)
        }


class FillAdmissionService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.lists = ListService(account_id=account_id, user_id=user_id)
        self.agents = AgentService(account_id=account_id, user_id=user_id)

    def admit(
        self,
        *,
        list_id: str,
        config: AgentConfig | None = None,
        agent_id: str = "",
        confirmed_row_count: int,
        test_run_id: str = "",
        concurrency: int = 0,
        rows: int = 0,
    ) -> Fill:
        """The one transaction. Exactly one of `config` (the quick tab:
        an ephemeral agent is created) or `agent_id` (a roster agent)
        is given; the serializer enforces the exclusivity, this method
        trusts it. `rows` scopes the fill to the FIRST N eligible rows
        (0 = all): the cutoff lands on the last targeted position, and
        rows past it stay not-attempted (the designed state a later
        refill extends).

        The List lock is taken LAST, and held only across what needs
        it: the columns write, and the bench seed, whose row write must
        follow the List lock (the delete paths' order) and whose type
        validation needs the claimed column. Everything before it (the
        guards, the eligible walk, the fill row, and the queue insert,
        which is one FillTask per targeted row and the expensive part
        of admission) touches no column, so none of it needs the list
        locked. A Postgres row lock cannot be released early, so the
        only way to hold it briefly is to acquire it late.

        What that costs is one wasted build when a deferred guard
        actually fires: the queue is inserted, the lock is taken, the
        columns disagree, and the whole transaction rolls back. Never a
        wrong result, and the preview keeps it to true RACES by running
        every deterministic refusal up front; the likeliest race is a
        double-submitted Fill button, whose loser wastes one build and
        hears the same-column refusal it should.

        What it buys is that a 50,000 row admission no longer blocks
        every other sheet-level write for the length of its insert. The
        List row lock is taken by column adds, renames, reorders and
        deletes, by add_rows, by the list delete, and by another
        admission. It is NOT taken by the worker: write_cells locks the
        ListRow and reads the list unlocked, for the column types only.

        The agent resolve and the model probe run before the
        transaction for a related reason: the probe is an HTTP call (a
        cold roster probe measured 1.9s healthy, and a dead source pays
        the list timeout), and nothing that slow belongs inside a
        transaction at all."""
        agent, resolved = self._resolve_agent(config=config, agent_id=agent_id)
        self._check_model(resolved)
        with transaction.atomic():
            target = self._list_or_raise(list_id)
            self._check_row_count(list_id, rows=rows, confirmed_row_count=confirmed_row_count)

            if agent is None:
                # The ephemeral row needs SOME label for custody
                # surfaces; the first output's is the least arbitrary.
                # Created BEFORE the guards because the append needs its
                # id and the three steps are one sequence; a refusal
                # rolls this row back with everything else.
                agent = self.agents.create_ephemeral(
                    label=resolved.outputs[0].label[:AGENT_LABEL_MAX_LENGTH], config=resolved
                )
            column_keys = self._preview_columns(target, config=resolved)
            eligible = self._iter_eligible_rows(target, prompt=resolved.prompt)
            targets = islice(eligible, rows) if rows else eligible
            fill, seed = self._open_fill(
                target,
                agent=agent,
                resolved=resolved,
                column_keys=column_keys,
                targets=targets,
                concurrency=concurrency,
                test_run_id=test_run_id,
            )
            if not fill.confirmed_row_count:
                raise NoEligibleRows()

            # The lock, last, over the writes that need it. The guards
            # run AGAIN here because the reads above were unlocked:
            # this is the judgement that counts, and the work above is
            # discarded with the transaction if it refuses. The seed
            # settles after the claim on purpose: its row write must
            # take the ListRow lock AFTER this List lock (the order
            # every delete path takes), and against the claimed column
            # so its type validator exists.
            locked = self._list_or_raise(list_id, lock=True)
            self._check_row_count(list_id, rows=rows, confirmed_row_count=confirmed_row_count)
            self._claim_columns(locked, config=resolved, agent_id=str(agent.id), fill_id=str(fill.id))
            fill = self._settle_fill(fill, locked, seed=seed)
        return fill

    def refill(
        self,
        *,
        list_id: str,
        column_key: str,
        rows: int = 0,
        resume_fill_id: str = "",
        confirmed_row_count: int = 0,
    ) -> Fill:
        """The ONE recovery primitive, admission-shaped: a NEW fill over
        the column's eligible rows without an answer (terminal outcomes
        are immutable, so recovery is never a reopened row). Unscoped,
        the cutoff is the CURRENT row count, so appended rows are
        covered and fill-remaining and resume are the same gesture;
        `rows` caps the target at the first N and lands the cutoff on
        the last targeted position (the next tranche of a scoped fill).
        The snapshot is FRESH on purpose: agent edits since the stopped
        fill apply, and blanks settled under a DIFFERENT config re-enter
        the target set (the changed prompt is a changed ask; see
        RefillTargets).

        Like admit, the shape is judged and the queue built against
        UNLOCKED reads, and the List lock comes last, over the claim
        and the settle alone; the locked pass re-runs the guards, and
        that re-judgement is the one that counts (a column deleted in
        between simply refuses there, rolling the built fill back).
        The agent resolve and the model probe also run before the
        transaction entirely: network IO must not hold any of it."""
        peek = self._list_or_raise(list_id)
        fill = self._require_fill_column(peek, column_key)
        try:
            agent = self.agents.get_for_fill(str(fill.get("agent_id", "")))
        except AgentNotFound as e:
            # Orphaned by an agent delete, which is allowed: answer in
            # the user's terms instead of 404-ing about an agent id
            # they never saw.
            raise ColumnAgentMissing() from e
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        resolved = agent.config()
        self._check_model(resolved)
        with transaction.atomic():
            # Unlocked while the shape is worked out and the queue is
            # built; the List lock comes at the end, over the claim and
            # the settle. Same reasoning as admit.
            target = self._list_or_raise(list_id)
            self._require_fill_column(target, column_key)
            # The stopped fill's shape, re-derived from the CURRENT
            # config (each output's own key is its column key). The
            # config is FRESH on purpose so agent edits apply, which
            # means the output set can differ from the one that built
            # these columns: a new output has to become a real column
            # here or its answers land nowhere a surface can read.
            owned = self._owned_keys(target, str(agent.id))
            column_keys = self._preview_columns(target, config=resolved, owned=owned)
            if column_key not in column_keys:
                # The URL names the column; the CONFIG names what the
                # new fill will write, and an output removed or renamed
                # since makes them disagree. Walking one column while
                # opening a fill that owns another re-targets every row
                # already answered in the second, spending a metered
                # completion on each that write_cells then refuses as
                # occupied.
                #
                # NOT FillColumnNotFound: that is for a column the
                # sheet does not have, and this one is right there in
                # front of the user, carrying a fill.
                raise ColumnNoLongerFilled(key=column_key)

            fingerprint = config_fingerprint(resolved)
            source: Fill | None = None
            if resume_fill_id:
                # CONTINUE means finish what THAT fill consented to,
                # never the column's whole remainder (the extend
                # gestures widen; resume does not), and under the
                # config it consented to (a changed prompt refuses).
                source = Fill.objects.filter(id=resume_fill_id, list_id=str(target.id)).first()
                if source is None:
                    raise ResumeJobNotFound()
                if source.config_fingerprint != fingerprint:
                    raise ResumeConfigChanged()
            # A RESUME judges owed-ness across the resumed fill's WHOLE
            # column set, a widening gesture across the one column the
            # user clicked. The fill owns every output its agent
            # declares, so Continue on a multi-output fill that looked
            # at one column would skip every row whose first column was
            # already answered and leave its siblings' retryable blanks
            # unreachable from that surface.
            # A fill always owns at least one column, so a resume's set
            # is never empty; no `or [column_key]` fallback, which could
            # only ever fire by silently NARROWING the resume.
            walked = source.column_keys if source is not None else [column_key]
            remaining = RefillTargets(
                target,
                column_keys=walked,
                fingerprint=fingerprint,
                prompt=resolved.prompt,
                owed_by=resume_fill_id,
            )
            # Eligibility and the resume bound both ride the targeting
            # pass, so this is one lazy stream: a scoped refill stops
            # at its N, and nothing behind it has been fetched.
            targets = islice(remaining, rows) if rows else remaining
            fill, seed = self._open_fill(
                target, agent=agent, resolved=resolved, column_keys=column_keys, targets=targets
            )
            if not fill.confirmed_row_count:
                # EMPTY is diagnosed first. A finished column consents
                # to nothing, and checking the echo before this made
                # 0 != N fire instead, so "Fill all remaining" on a
                # done column reported that the sheet now has 0 rows,
                # and NoEligibleRows became unreachable for any caller
                # that echoed at all, which is the whole point of the
                # dropped flag. The walk ran to the end here, so the
                # flag is settled and can name WHICH filter emptied it.
                if remaining.dropped_any:
                    raise NoEligibleRows()
                raise RefillEmpty()
            # The consent echo, checked AFTER the walk and inside the
            # transaction, because the number the user was shown is a
            # count of what this click would SPEND and only the walk
            # knows that. Nothing has run yet; the refusal rolls the
            # fill back with everything else.
            #
            # UNSCOPED asks only, the same rule admit follows: a scoped
            # ask names its own N and never showed a total. And GROWTH
            # only, also admit's rule: the number is a spend ceiling,
            # so fewer owed rows than reviewed is a cheaper answer to
            # the same question, never drift worth refusing.
            if rows == 0 and confirmed_row_count and fill.confirmed_row_count > confirmed_row_count:
                raise TargetCountChanged(fill.confirmed_row_count)

            # The lock, last, over the claim and the settle. The guards
            # that read the array run AGAIN here, against the locked
            # copy, because everything above judged an unlocked read;
            # a refusal rolls the fill and its queue back with it.
            locked = self._list_or_raise(list_id, lock=True)
            self._require_fill_column(locked, column_key)
            self._claim_columns(
                locked,
                config=resolved,
                agent_id=str(agent.id),
                owned=self._owned_keys(locked, str(agent.id)),
                fill_id=str(fill.id),
            )
            fill = self._settle_fill(fill, locked, seed=seed)
        return fill

    def _open_fill(
        self,
        target: List,
        *,
        agent: Agent,
        resolved: AgentConfig,
        column_keys: list[str],
        targets: Iterator[tuple[str, int]],
        concurrency: int = 0,
        test_run_id: str = "",
    ) -> tuple[Fill, tuple[AgentTestRun, int] | None]:
        """Everything after the DECISION, shared by both admission
        paths: the fill row carrying its frozen config, the QUEUE, and
        the DETECTION of the bench seed (returned, not applied: the
        walk is the only thing that knows the borrowed row's position,
        and the caller settles it under the List lock, after the
        claim). Runs UNLOCKED, inside the caller's transaction: the
        transaction is what makes the column and the fill land
        together; the lock comes later and covers only the writes that
        need it.

        `targets` is CONSUMED, in FILL_WRITE_BATCH steps: nothing here
        holds the sheet in memory, and a 50,000 row fill peaks at one
        batch rather than a dict of every row plus a list of every task
        built from it. The count is therefore only known at the end, so
        `confirmed_row_count` is stamped after the walk, in this same
        transaction.

        A caller reads `confirmed_row_count == 0` as "nothing to do"
        and raises its own refusal: which one that is depends on why
        the stream was empty, and only the caller knows that.

        NOTHING is written to the sheet. A targeted cell shimmers
        because a QUEUED task says so, which is why cancelling needs no
        sweep and why a 25,000 row fill does not write 50,000 cell rows
        on a click."""
        fill = Fill.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
            list_id=str(target.id),
            agent_id=str(agent.id),
            column_keys=column_keys,
            config_snapshot=resolved.model_dump(),
            config_fingerprint=config_fingerprint(resolved),
            concurrency=concurrency,
            confirmed_row_count=0,
        )
        # The bench row is resolved BEFORE the walk so the walk can
        # recognise it in passing: it is consented like any other row,
        # but it is already answered, so it lands DONE instead of
        # queued and is never re-billed.
        borrowed = self._borrowed_row(config=resolved, test_run_id=test_run_id)
        seeded_at: int | None = None
        consented = 0
        cap = self._free_door_row_cap(resolved)
        # strict=False: the last page is short whenever the target count
        # is not a multiple of the batch, which is the normal case.
        for page in batched(targets, FILL_WRITE_BATCH, strict=False):
            tasks = []
            for row_id, position in page:
                consented += 1
                # Free-door budget, checked AS the walk counts rather
                # than against a total nobody has yet. Per ROW, not per
                # page, so the refusal names the count that crossed the
                # cap instead of wherever the page happened to end. It
                # refuses inside the transaction, so nothing lands.
                if consented > cap:
                    raise FreeSearchBudget(searches=MAX_TOOL_CALLS * consented)
                if borrowed is not None and row_id == borrowed.row_id:
                    seeded_at = position
                    continue
                tasks.append(
                    FillTask(account_id=self.account_id, fill_id=str(fill.id), row_id=row_id, position=position)
                )
            # No ignore_conflicts: the fill id is minted just above, so
            # nothing else can hold a task under it and a duplicate
            # could only mean the target stream yielded a row twice.
            # Swallowing that would leave confirmed_row_count, which is
            # the progress denominator on every surface, disagreeing
            # with the queue it counts.
            FillTask.objects.bulk_create(tasks)

        # No columns write here. The caller claims them AFTER this
        # returns, under the List lock, in one write that carries both
        # the agent link and this fill's id: the queue insert is the
        # expensive part of admission and it has no business happening
        # between two writes to the same array.

        Fill.objects.filter(id=fill.id).update(confirmed_row_count=consented)
        fill.refresh_from_db()
        # The seed is DETECTED here (only the walk knows the borrowed
        # row's position) but APPLIED by _settle_fill, under the List
        # lock and after the claim. It must be: the seed's row write
        # takes a ListRow lock, which may only follow the List lock
        # (the order every delete path takes; the reverse deadlocks),
        # and its type validation reads the columns array, which
        # carries the new column only once the claim has written it.
        seed = (borrowed, seeded_at) if borrowed is not None and seeded_at is not None else None
        return fill, seed

    @staticmethod
    def _iter_eligible_rows(target: List, *, prompt: str) -> Iterator[tuple[str, int]]:
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
                ListRow.objects.filter(list_id=str(target.id), position__gt=after)
                .order_by("position")
                .only("id", "position", "data")[:FILL_SCAN_CHUNK]
            )
            if not chunk:
                return
            for row in chunk:
                if not variables or _row_is_eligible(row.data, variables):
                    yield str(row.id), row.position
            after = chunk[-1].position

    def _resolve_agent(self, *, config: AgentConfig | None, agent_id: str) -> tuple[Agent | None, AgentConfig]:
        if config is not None:
            return None, config
        # Roster-only on purpose: an ephemeral row belongs to exactly
        # one column, so a second column can never point at it.
        agent = self.agents.get(agent_id)
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        return agent, agent.config()

    def _list_or_raise(self, list_id: str, *, lock: bool = False) -> List:
        """The account-scoped list read, locked only when the caller is
        about to decide something on it."""
        qs = List.objects.select_for_update() if lock else List.objects
        try:
            return qs.get(id=list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(list_id) from e

    @staticmethod
    def _check_model(config: AgentConfig) -> None:
        """Add-time UX only; the worker's claim-time resolution is
        authoritative (a source can die mid-fill either way)."""
        try:
            model_for(config.provider, config.source, config.model)
        except ModelUnavailable as e:
            raise ModelUnrunnable(str(e)) from e

    def _claim_columns(
        self,
        target: List,
        *,
        config: AgentConfig,
        agent_id: str,
        fill_id: str,
        owned: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Resolve, guard, append: the ONE sequence that turns a set of
        outputs into columns a fill may write, run by BOTH admission
        paths because both open a fill that writes cells.

        Refill used to take the keys straight off the config and skip
        all three. A roster agent's outputs can change between fills
        (the snapshot is re-derived on purpose so edits apply), so a
        refill could open a fill owning a column the sheet did not
        have: every row spent a completion, the value landed in
        ListRow.data under a key no surface renders, and the cell
        records were filtered out of every count. It skipped the
        reserved-key and collision refusals for the same reason.

        `owned` names the keys the caller has already established it
        may write; see the collision rule below."""
        # The LIVE-FILL refusal goes first, before existence. A second
        # admit on a column a fill is already writing is exactly that,
        # and saying "this sheet already has that column" instead would
        # be true and useless: the column is there because the fill the
        # user just started put it there.
        # The fill being opened is one value wearing two roles: the id
        # the guards must EXCLUDE (it is already live) and the id the
        # claimed columns record as current. One REQUIRED parameter,
        # because a default here would stamp current_fill_id="" (the
        # contract's "column predates the write") silently.
        self._check_columns_free(target, column_keys=[output.key for output in config.outputs], opening=fill_id)
        column_keys = self._resolve_columns(target, config=config, owned=owned)
        self._check_guards(target, column_keys=column_keys, opening=fill_id)
        # No retype set and no occupancy probe: a column that exists
        # keeps the type it was created with, and resolution above has
        # already refused both an existing key we do not own and an
        # owned one whose output changed shape.
        self._append_columns(target, column_keys=column_keys, config=config, agent_id=agent_id, fill_id=fill_id)
        return column_keys

    @staticmethod
    def _owned_keys(target: List, agent_id: str) -> frozenset[str]:
        """The keys this agent already fills on this sheet: what a
        refill may write without the existence rule refusing its own
        columns. ONE derivation, called by the unlocked pass and the
        locked one, because two hand-spelled copies drifting by a typo
        is exactly the failure mode a double-judgment design invites."""
        return frozenset(
            column["key"] for column in target.columns if (column.get("fill") or {}).get("agent_id") == agent_id
        )

    @staticmethod
    def _require_fill_column(target: List, column_key: str) -> dict:
        """The named column's fill member, or the 404-shaped refusal
        (a column the sheet does not have, or a plain one, is not a
        refill target)."""
        fill = next(
            (column.get("fill") for column in target.columns if column["key"] == column_key and column.get("fill")),
            None,
        )
        if fill is None:
            raise FillColumnNotFound(column_key)
        return fill

    def _preview_columns(self, target: List, *, config: AgentConfig, owned: frozenset[str] = frozenset()) -> list[str]:
        """The keys this fill will own, judged WITHOUT taking a lock.

        The keys themselves come from the OUTPUTS, never from the
        sheet, so this cannot disagree with what the locked claim
        decides; the sheet only decides whether to REFUSE, and this
        runs the refusals that cost nothing so an admission doomed by
        an existing column does not build a queue first.

        It mirrors the claim's order (live fills before existence, so
        a second admit on a column a fill is writing hears that, not
        the useless "this sheet already has that column"), and it runs
        every DETERMINISTIC refusal: an account at its fill cap or a
        sheet at its column cap would otherwise build up to 50,000
        FillTask rows before hearing a "no" that was knowable up
        front, every time rather than rarely. The cap is read through
        live_fill_count, the lock-free count the bench lane shares;
        the select_for_update half of the cap stays locked-only,
        because taking fill-row locks here would invert the
        List-then-Fill order its serialization depends on. The claim,
        under the List lock at the end of admission, is the judgement
        that counts."""
        self._check_columns_free(target, column_keys=[output.key for output in config.outputs])
        keys = self._resolve_columns(target, config=config, owned=owned)
        # The cap BEFORE the column arithmetic, mirroring _check_guards:
        # an account at both caps must hear fills_full (a 409, "wait
        # for one to finish") and not columns_full (a 400, "change the
        # request"), because waiting actually fixes the first and the
        # second would send them off to delete columns for nothing.
        if live_fill_count(self.account_id) >= MAX_ACTIVE_FILLS:
            raise AccountFillsFull()
        self._check_column_cap(target, column_keys=keys)
        return keys

    def _check_row_count(self, list_id: str, *, rows: int, confirmed_row_count: int) -> None:
        """The sheet has rows, and it has the count the user consented
        to. Run unlocked to fail fast and again under the lock, where
        the List lock (not this read) is what makes the second answer
        authoritative; one body so the two passes cannot drift."""
        row_count = ListRow.objects.filter(list_id=list_id).count()
        if row_count == 0:
            raise EmptyFill()
        # The consent echo guards the sheet total the user READ, which
        # a scoped fill never shows: it asked for the first N usable
        # rows, and sheet growth cannot change what N means. GROWTH
        # only: the count is a spend ceiling, so only more rows than
        # consented refuses; fewer just fills less.
        if rows == 0 and row_count > confirmed_row_count:
            raise RowCountChanged(row_count)

    def _resolve_columns(self, target: List, *, config: AgentConfig, owned: frozenset[str] = frozenset()) -> list[str]:
        """The columns this fill will own. Each output's OWN key IS its
        column key, single and multi alike (the outputs ARE the
        columns), which is why this returns a LIST and not a mapping.
        Each key must be one this fill already owns or one no column
        holds; any other existing key refuses, whether or not it has
        values in it. Absent keys become new columns."""
        keys: list[str] = []
        claimed: dict[str, str] = {}
        for output in config.outputs:
            key = output.key
            if not key or reserved_output_key(key):
                raise ReservedColumnKey(label=output.label)
            if key in claimed:
                raise DerivedKeyCollision(first=claimed[key], second=output.label)
            claimed[key] = output.label
            keys.append(key)
        stored_types = {column["key"]: column.get("type", "") for column in target.columns}
        filled_keys = {column["key"] for column in target.columns if column.get("fill")}
        outputs_by_key = {output.key: output for output in config.outputs}
        for key in keys:
            # `owned` is what the caller has already established it may
            # write: a refill's own columns exist BECAUSE it made them,
            # so the existence rule would otherwise refuse every refill.
            if key not in owned:
                if key in stored_types:
                    raise ColumnCollision(key=key, filled=key in filled_keys)
                continue
            wanted = outputs_by_key[key].type
            if stored_types.get(key, wanted) != wanted:
                raise ColumnTypeChanged(key=key, stored=stored_types[key], wanted=wanted)
        return keys

    def _check_guards(self, target: List, *, column_keys: list[str], opening: str = "") -> None:
        # columns-free is checked by the caller BEFORE resolution, so
        # the live-fill refusal wins over the existence one.
        self._check_account_cap(opening=opening)
        self._check_column_cap(target, column_keys=column_keys)

    @staticmethod
    def _check_column_cap(target: List, *, column_keys: list[str]) -> None:
        """ONE spelling of the column-cap arithmetic, called by the
        preview and the locked claim: two hand-spelled copies are how
        the passes drift, and this file has the receipts."""
        new_keys = set(column_keys) - {column["key"] for column in target.columns}
        if len(target.columns) + len(new_keys) > MAX_LIST_COLUMNS:
            raise ColumnsFull()

    @staticmethod
    def _check_columns_free(target: List, *, column_keys: list[str], opening: str = "") -> None:
        """`opening` is the fill this admission just created, if the
        claim runs after it. Admission opens the fill BEFORE taking the
        List lock, so without this the guard finds our own live fill on
        our own column and refuses the admission to itself."""
        live = Fill.objects.filter(list_id=str(target.id), status__in=LIVE_FILL_STATUSES).exclude(id=opening)
        taken = {key for fill in live for key in fill.column_keys or ()}
        if taken & set(column_keys):
            raise SameColumnFillActive()

    def _check_account_cap(self, *, opening: str = "") -> None:
        # The account cap must serialize ACROSS lists (the List lock
        # only covers same-list admits): lock the account's live fill
        # rows in id order so concurrent admits at the cap boundary
        # queue here, then count in a fresh statement, which sees fills
        # committed while this one waited on the locks. Lock order is
        # List row first (the caller's own lock), then fill rows; the
        # worker's fill-row locks run in their own transactions with no
        # List lock held, so the order cannot invert. A burst of
        # first-ever admits on an idle account has nothing to lock and
        # can still overshoot, bounded by the simultaneous requests.
        list(
            Fill.objects.select_for_update()
            .filter(account_id=self.account_id, status__in=LIVE_FILL_STATUSES)
            .exclude(id=opening)
            .order_by("id")
            .only("id")
        )
        # EXCLUDED from the count as well as the locks above: this
        # admission's own fill is already live by the time the cap is
        # judged, and counting it would refuse one fill early.
        account_live = (
            Fill.objects.filter(account_id=self.account_id, status__in=LIVE_FILL_STATUSES).exclude(id=opening).count()
        )
        if account_live >= MAX_ACTIVE_FILLS:
            raise AccountFillsFull()

    @staticmethod
    def _free_door_row_cap(config: AgentConfig) -> int:
        """How many rows this fill may consent to before the FREE search
        door's budget refuses it. A CAP rather than a check on a total,
        because admission counts its rows as it walks them and never
        holds the whole set to measure it. Unbounded when the door is
        metered or the config uses no tools."""
        if config.uses_tools and _fill_search_door_is_free():
            return FREE_SEARCH_FILL_BUDGET // MAX_TOOL_CALLS
        return MAX_LIST_ROWS

    @staticmethod
    def _append_columns(
        target: List, *, column_keys: list[str], config: AgentConfig, agent_id: str, fill_id: str
    ) -> None:
        """THE one columns write of an admission: new columns append
        with the fill link and the output's type, an existing one gains
        the link, and every claimed column learns which fill now speaks
        for it.

        Both facts ride ONE write because the fill row already exists
        when this runs: admission opens the fill and its queue before
        taking the List lock, so there is nothing left to fill in
        afterwards. Storing `current_fill_id` here rather than
        re-deriving it per read is what keeps the four second poll off
        a walk of every fill the sheet has ever had, newest first.

        A column NEVER changes type here. Type is not display-only at
        the write seam, where write_cells gates every value through the
        column's validator, so a retype under existing answers makes
        every later one TYPE_MISMATCH. Resolution refuses the case
        rather than choosing which way to be wrong."""
        outputs_by_key = {output.key: output for output in config.outputs}
        columns = [dict(column) for column in target.columns]
        existing = {column["key"] for column in columns}
        for column in columns:
            output = outputs_by_key.get(column["key"])
            if output is not None and column["key"] in column_keys:
                column["fill"] = {"agent_id": agent_id, "current_fill_id": fill_id}
        for key in column_keys:
            if key in existing:
                continue
            # Marks the key appended, so one key can never land twice
            # in a single call (column resolution refuses collisions
            # upstream; this is the write-side backstop).
            existing.add(key)
            output = outputs_by_key[key]
            columns.append(
                {
                    "key": key,
                    "label": output.label[:COLUMN_LABEL_MAX_LENGTH],
                    "type": output.type,
                    "fill": {"agent_id": agent_id, "current_fill_id": fill_id},
                }
            )
        target.columns = columns
        target.save(update_fields=["columns", "updated_at"])

    def _borrowed_row(self, *, config: AgentConfig, test_run_id: str) -> AgentTestRun | None:
        """The bench run this fill may seed from, or None. The prewrite
        ECONOMY, never a gate: it seeds only when the stored run is
        complete, ours, and config-identical, and on any miss the row
        simply runs normally."""
        if not test_run_id:
            return None
        run = AgentTestRun.objects.filter(
            id=test_run_id, account_id=self.account_id, status=TestRunStatus.COMPLETE
        ).first()
        if run is None or not run.row_id or run.config_fingerprint != config_fingerprint(config):
            return None
        # A run stored under a retired cause vocabulary is one more
        # MISS, never an error: the row simply runs fresh under the
        # current words, and the one writer of cell truth stays
        # strict.
        known = {state.value for state in StoredCellState}
        for key in ("blank_cause", "declined_cause"):
            value = (run.result or {}).get(key, "")
            if value and value not in known:
                return None
        return run

    def _settle_fill(self, fill: Fill, target: List, *, seed: tuple[AgentTestRun, int] | None) -> Fill:
        """The locked tail of both admission paths: apply the bench
        seed if the walk found one, then ask whether the fill was born
        drained. Runs AFTER _claim_columns, under the List lock, so the
        seed's row write takes its ListRow lock in the same List-then-
        row order every delete path takes, and write_cells sees the
        claimed column's type instead of missing it."""
        if seed is not None:
            run, position = seed
            counters = self._seed_borrowed_row(fill, run=run, position=position)
            # DELTAS and F(), the same shape the worker's bump uses:
            # assignment would work today (nothing outside this
            # transaction can see the row yet), which is exactly the
            # reason not to write it that way.
            Fill.objects.filter(id=fill.id).update(**{key: models.F(key) + delta for key, delta in counters.items()})
        if fill.confirmed_row_count:
            # A fill can be born drained: every row it consented to was
            # answered on the bench, so nothing is claimable and no
            # worker would ever visit it. That is the same question the
            # worker asks after its last row, so it is the same rule,
            # not a copy of it. Guarded on the count because a fill
            # with no rows is a REFUSAL the caller is about to raise,
            # never a completion.
            try_finish(str(fill.id))
        fill.refresh_from_db()
        return fill

    def _seed_borrowed_row(self, fill: Fill, *, run: AgentTestRun, position: int) -> dict[str, int]:
        """Write the borrowed row's answers and close its task DONE,
        carrying the bench run's own result, so the row is never queued
        and never re-billed and the drawer reads that run exactly where
        it reads every other.

        A seeded row is a RESOLVED row and has to look like one from
        every angle, so it lands through the same landing the worker's
        terminal path uses (the sheet value, the cell truth, the task
        close, one transaction); only the close differs: a task
        created DONE, never one claimed. Writing the value alone would
        leave a cell no FillCellState speaks for, and absence means
        NEVER ATTEMPTED, so the cell would read as untouched work that
        no refill can reach (its value excludes it from targeting).

        Returns the counter deltas rather than bumping, so the caller
        folds them into the one update that also stamps the row count."""
        result = TestRunService.result_for(run) or CellRunResult()

        def create_done(stored: dict) -> bool:
            FillTask.objects.create(
                account_id=fill.account_id,
                fill_id=str(fill.id),
                row_id=run.row_id,
                position=position,
                status=FillTaskStatus.DONE,
                result=stored,
            )
            return True

        landed = land_row(fill, run.row_id, result, close=create_done, lists=self.lists)
        if landed is None:
            raise RuntimeError("a created task cannot miss its close")
        return landed.deltas(was_parked=False)

"""The NORMAL kind's admission service. admit() is the column add:
caps and refusals, column resolution (match-or-refuse), the config
snapshot, the ephemeral-agent create, the row-count echo, the fill
row, and the bulk insert of the queue.
refill() is the one RECOVERY primitive, admission-shaped: a NEW fill
over the column's unanswered rows. Everything here goes through
ONE-transaction methods, and the columns write lands in the same
transaction as the fill and its queue: anything less can append a
column whose fill never lands, leaving the sheet carrying a column
nothing will ever fill. The List lock is taken LATE inside that
transaction, over the columns write alone; admit()'s docstring
carries the why. Account-scoped like every lists service."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import batched, islice

from django.db import transaction
from django.utils import timezone

from agents.models import Agent
from agents.services import AgentNotFound, AgentService
from openbower_schema.agents import LABEL_MAX_LENGTH as AGENT_LABEL_MAX_LENGTH
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig

from ...constants import FILL_WRITE_BATCH, NodeRunStatus
from ...models import Fill, List, ListRow, NodeRun
from ..fingerprint import config_fingerprint
from ..lists import ListNotFound
from .base import AdmissionBase
from .columns import claim_columns, owned_keys, preview_columns, require_fill_column
from .errors import (
    ColumnAgentMissing,
    ColumnNoLongerFilled,
    EmptyFill,
    FreeSearchBudget,
    NoEligibleRows,
    ProviderRetiredRefusal,
    RefillEmpty,
    ResumeConfigChanged,
    ResumeRunNotFound,
    RowCountChanged,
    TargetCountChanged,
)
from .targets import RefillTargets, free_provider_row_cap, iter_eligible_rows


class FillAdmissionService(AdmissionBase):
    def __init__(self, *, account_id: str, user_id: str) -> None:
        super().__init__(account_id=account_id, user_id=user_id)
        self.agents = AgentService(account_id=account_id)

    def admit(
        self,
        *,
        list_id: str,
        config: AgentConfig | None = None,
        agent_id: str = "",
        confirmed_row_count: int,
        rows: int = 0,
    ) -> Fill:
        """The one transaction. Exactly one of `config` (the quick tab:
        an ephemeral agent is created) or `agent_id` (a roster agent)
        is given; the serializer enforces the exclusivity, this method
        trusts it. `rows` scopes the fill to the FIRST N eligible rows
        (0 = all): the cutoff lands on the last targeted position, and
        rows past it stay not-attempted (the designed state a later
        refill extends).

        The List lock is taken LAST, and held only across the one
        write that needs it: the columns write. Everything before it (the
        guards, the eligible walk, the fill row, and the queue insert,
        which is one NodeRun per targeted row and the expensive part
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
        agent, resolved_config = self._resolve_agent(config=config, agent_id=agent_id)
        self._check_model(resolved_config)
        with transaction.atomic():
            target_list = self._list_or_raise(list_id)
            self._check_row_count(list_id, rows=rows, confirmed_row_count=confirmed_row_count)

            if agent is None:
                # The ephemeral row needs SOME label for custody
                # surfaces; the first output's is the least arbitrary.
                # Created BEFORE the guards because the append needs its
                # id and the three steps are one sequence; a refusal
                # rolls this row back with everything else.
                agent = self.agents.create_ephemeral(
                    owner_id=self.user_id,
                    label=resolved_config.outputs[0].label[:AGENT_LABEL_MAX_LENGTH],
                    config=resolved_config,
                )
            column_keys = preview_columns(target_list, config=resolved_config, account_id=self.account_id)
            eligible = iter_eligible_rows(target_list, prompt=resolved_config.prompt)
            targets = islice(eligible, rows) if rows else eligible
            fill = self._open_fill(
                target_list,
                agent=agent,
                resolved_config=resolved_config,
                column_keys=column_keys,
                targets=targets,
            )
            if not fill.confirmed_row_count:
                raise NoEligibleRows()

            # The lock, last, over the one write that needs it. The
            # guards run AGAIN here because the reads above were
            # unlocked: this is the judgement that counts, and the work
            # above is discarded with the transaction if it refuses.
            locked = self._list_or_raise(list_id, lock=True)
            self._check_row_count(list_id, rows=rows, confirmed_row_count=confirmed_row_count)
            claim_columns(
                locked,
                config=resolved_config,
                agent_id=str(agent.id),
                fill_run_id=str(fill.id),
                account_id=self.account_id,
            )
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
        fill = require_fill_column(peek, column_key)
        try:
            agent = self.agents.get_for_fill(str(fill.get("agent_id", "")))
        except AgentNotFound as e:
            # Orphaned by an agent delete, which is allowed: answer in
            # the user's terms instead of 404-ing about an agent id
            # they never saw.
            raise ColumnAgentMissing() from e
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        resolved_config = agent.config()
        self._check_model(resolved_config)
        with transaction.atomic():
            # Unlocked while the shape is worked out and the queue is
            # built; the List lock comes at the end, over the claim and
            # the settle. Same reasoning as admit.
            target_list = self._list_or_raise(list_id)
            require_fill_column(target_list, column_key)
            # The stopped fill's shape, re-derived from the CURRENT
            # config (each output's own key is its column key). The
            # config is FRESH on purpose so agent edits apply, which
            # means the output set can differ from the one that built
            # these columns: a new output has to become a real column
            # here or its answers land nowhere a surface can read.
            owned = owned_keys(target_list, str(agent.id))
            column_keys = preview_columns(target_list, config=resolved_config, account_id=self.account_id, owned=owned)
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

            fingerprint = config_fingerprint(resolved_config)
            source: Fill | None = None
            if resume_fill_id:
                # CONTINUE means finish what THAT fill consented to,
                # never the column's whole remainder (the extend
                # gestures widen; resume does not), and under the
                # config it consented to (a changed prompt refuses).
                source = Fill.objects.filter(id=resume_fill_id, list_id=str(target_list.id)).first()
                if source is None:
                    raise ResumeRunNotFound()
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
                target_list,
                column_keys=walked,
                fingerprint=fingerprint,
                prompt=resolved_config.prompt,
                owed_by=resume_fill_id,
            )
            # Eligibility and the resume bound both ride the targeting
            # pass, so this is one lazy stream: a scoped refill stops
            # at its N, and nothing behind it has been fetched.
            targets = islice(remaining, rows) if rows else remaining
            fill = self._open_fill(
                target_list, agent=agent, resolved_config=resolved_config, column_keys=column_keys, targets=targets
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

            # The lock, last, over the claim. The guards that read the
            # array run AGAIN here, against the locked copy, because
            # everything above judged an unlocked read; a refusal rolls
            # the fill and its queue back with it.
            locked = self._list_or_raise(list_id, lock=True)
            require_fill_column(locked, column_key)
            claim_columns(
                locked,
                config=resolved_config,
                agent_id=str(agent.id),
                fill_run_id=str(fill.id),
                account_id=self.account_id,
                owned=owned_keys(locked, str(agent.id)),
            )
        return fill

    def _open_fill(
        self,
        target_list: List,
        *,
        agent: Agent,
        resolved_config: AgentConfig,
        column_keys: list[str],
        targets: Iterator[tuple[str, int]],
    ) -> Fill:
        """Everything after the DECISION, shared by both admission
        paths: the fill row carrying its frozen config, and the QUEUE.
        Runs UNLOCKED, inside the caller's transaction: the
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
            list_id=str(target_list.id),
            agent_id=str(agent.id),
            column_keys=column_keys,
            config_snapshot=resolved_config.model_dump(),
            config_fingerprint=config_fingerprint(resolved_config),
            confirmed_row_count=0,
        )
        consented = 0
        cap = free_provider_row_cap(resolved_config)
        # Tasks are born READY (the manual provisioner moves READY ->
        # QUEUED when it publishes), stamped for the reclaim scan/audit from
        # the start, carrying the fill's agent so a fill-backed task is
        # self-describing like an autofill one.
        now = timezone.now()
        # strict=False: the last page is short whenever the target count
        # is not a multiple of the batch, which is the normal case.
        for page in batched(targets, FILL_WRITE_BATCH, strict=False):
            tasks = []
            for row_id, position in page:
                consented += 1
                # Free-provider budget, checked AS the walk counts rather
                # than against a total nobody has yet. Per ROW, not per
                # page, so the refusal names the count that crossed the
                # cap instead of wherever the page happened to end. It
                # refuses inside the transaction, so nothing lands.
                if consented > cap:
                    raise FreeSearchBudget(searches=MAX_TOOL_CALLS * consented)
                tasks.append(
                    NodeRun(
                        account_id=self.account_id,
                        fill_run_id=str(fill.id),
                        agent_id=str(agent.id),
                        row_id=row_id,
                        list_id=fill.list_id,
                        position=position,
                        status=NodeRunStatus.READY,
                        last_state_change_at=now,
                    )
                )
            # No ignore_conflicts: the fill id is minted just above, so
            # nothing else can hold a task under it and a duplicate
            # could only mean the target stream yielded a row twice.
            # Swallowing that would leave confirmed_row_count, which is
            # the progress denominator on every surface, disagreeing
            # with the queue it counts.
            NodeRun.objects.bulk_create(tasks)

        # No columns write here. The caller claims them AFTER this
        # returns, under the List lock, in one write that carries both
        # the agent link and this fill's id: the queue insert is the
        # expensive part of admission and it has no business happening
        # between two writes to the same array.

        Fill.objects.filter(id=fill.id).update(confirmed_row_count=consented)
        fill.refresh_from_db()
        return fill

    def _list_or_raise(self, list_id: str, *, lock: bool = False) -> List:
        """The account-scoped list read, locked only when the caller is
        about to decide something on it."""
        qs = List.objects.select_for_update() if lock else List.objects
        try:
            return qs.get(id=list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(list_id) from e

    def _resolve_agent(self, *, config: AgentConfig | None, agent_id: str) -> tuple[Agent | None, AgentConfig]:
        if config is not None:
            return None, config
        # Roster-only on purpose: an ephemeral row belongs to exactly
        # one column, so a second column can never point at it.
        agent = self.agents.get(agent_id)
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        return agent, agent.config()

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

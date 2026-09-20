"""The NORMAL kind's admission service. admit() is the column add:
caps and refusals, column resolution (match-or-refuse), the config
snapshot, the ephemeral-agent create, the fill row, and the walk that
queues its runs. refill() is the one RECOVERY primitive,
admission-shaped: a NEW fill over the column's unanswered rows.

Admission DECIDES and QUEUES; it does not walk the sheet. Everything
here goes through ONE-transaction methods, and the columns write lands
in the same transaction as the fill row and the job that will queue its
runs: anything less can append a column whose fill never lands, leaving
the sheet carrying a column nothing will ever fill. The walk itself
(one page per slice, the agent processor judging each row) is the
`enqueue_runs` job, worked within seconds by the jobs container, so a
50,000 row consent costs the request nothing but a probe for its first
row. Account-scoped like every lists service."""

from __future__ import annotations

from django.db import transaction

from agents.models import Agent
from agents.services import AgentNotFound, AgentService
from jobs.services import enqueue
from openbower_schema.agents import LABEL_MAX_LENGTH as AGENT_LABEL_MAX_LENGTH
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig

from ...jobs.enqueue_runs import EnqueueRuns
from ...models import Fill, List, ListRow, Node
from ...processors import WalkMode, WalkScope, processor_for
from ..fingerprint import config_fingerprint
from ..lists import ListNotFound
from ..workflows import WorkflowService, agent_id_of, columns_for_node
from .base import AdmissionBase
from .columns import claim_columns, preview_columns, require_fill_column
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
)
from .targets import free_provider_row_cap


class FillAdmissionService(AdmissionBase):
    def __init__(self, *, account_id: str, user_id: str) -> None:
        super().__init__(account_id=account_id, user_id=user_id)
        self.agents = AgentService(account_id=account_id)
        self.workflows = WorkflowService(account_id=account_id)

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
        trusts it.

        A fill is a CONSENT, so its walk has a fixed range: the rows at
        or below `confirmed_row_count`, the count the user was shown.
        Positions are dense and append-only, so a row appended after the
        click sits above the range and is never walked; it shows unfilled
        and the next refill takes it. `rows` scopes the fill to the FIRST
        N eligible rows within that range (0 = all); rows past the last
        targeted one stay not-attempted (the designed state a later
        refill extends).

        Two judgements that used to need the walk are made from the
        consent instead: the free-search budget refuses on the consented
        count, before anything is written; and a probe for the FIRST
        eligible row refuses a sheet the prompt cannot act on. The
        denominator is stamped from the consent and settles to the runs
        actually queued when the walk ends.

        The List lock is taken LAST, over the one write that needs it:
        the columns write. The node and workflow get-or-create is the
        transaction's first write on a key a concurrent admission can
        collide on, and Postgres holds the uncommitted unique-index entry
        until commit; with no walk inside the transaction that hold is
        milliseconds. The agent resolve and the model probe run before
        the transaction: the probe is an HTTP call (a cold roster probe
        measured 1.9s healthy), and nothing that slow belongs inside a
        transaction at all."""
        agent, resolved_config = self._resolve_agent(config=config, agent_id=agent_id)
        self._check_model(resolved_config)
        with transaction.atomic():
            target_list = self._list_or_raise(list_id)
            row_count = self._require_rows(list_id)
            if agent is None:
                # The ephemeral row needs SOME label for custody
                # surfaces; the first output's is the least arbitrary.
                # Created BEFORE the guards because the node needs its
                # id; a refusal rolls this row back with everything else.
                agent = self.agents.create_ephemeral(
                    owner_id=self.user_id,
                    label=resolved_config.outputs[0].label[:AGENT_LABEL_MAX_LENGTH],
                    config=resolved_config,
                )
            # The node binds this agent to this sheet: get-or-create, so
            # a second column from the same roster agent reuses it (one
            # node per agent per sheet is the run's own grain).
            node = self.workflows.get_or_create_column_agent_node(target_list, agent_id=str(agent.id))
            column_keys = preview_columns(target_list, config=resolved_config, account_id=self.account_id)
            until_position = row_count if rows else min(confirmed_row_count or row_count, row_count)
            consented = min(rows, until_position) if rows else until_position
            self._check_budget(resolved_config, consented=consented)
            fill = self._open_fill(
                target_list, node=node, resolved_config=resolved_config, column_keys=column_keys, consented=consented
            )
            scope = WalkScope(mode=WalkMode.FRESH, fill_run_id=str(fill.id), until_position=until_position, limit=rows)
            if (
                not processor_for(account_id=self.account_id, node=node, scope=scope)
                .probe(target_list, until_position=until_position)
                .found
            ):
                raise NoEligibleRows()
            self._queue_walk(target_list, node=node, scope=scope)

            # The lock, last, over the one write that needs it. The
            # guards run AGAIN here because the reads above were
            # unlocked: this is the judgement that counts, and the work
            # above is discarded with the transaction if it refuses.
            locked = self._list_or_raise(list_id, lock=True)
            claim_columns(
                locked,
                config=resolved_config,
                node_id=str(node.id),
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
        are immutable, so recovery is never a reopened row). The range is
        the count the user was shown when one was (`confirmed_row_count`),
        else the sheet as it stands; `rows` caps the target at the first
        N (the next tranche of a scoped fill). The snapshot is FRESH on
        purpose: agent edits since the stopped fill apply, and blanks
        settled under a DIFFERENT config re-enter the target set (the
        changed prompt is a changed ask).

        Like admit, the shape is judged against UNLOCKED reads and the
        List lock comes last, over the claim alone; the locked pass
        re-runs the guards, and that re-judgement is the one that counts.
        The agent resolve and the model probe run before the transaction
        entirely: network IO must not hold any of it."""
        peek = self._list_or_raise(list_id)
        column = require_fill_column(peek, column_key)
        # A missing NODE raises as the corruption it is (nodes die only
        # with their list); a missing AGENT is the allowed orphaning.
        node = self.workflows.get_node(column.node_id)
        try:
            agent = self.agents.get_for_fill(agent_id_of(node))
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
            target_list = self._list_or_raise(list_id)
            require_fill_column(target_list, column_key)
            row_count = self._require_rows(list_id)
            # The stopped fill's shape, re-derived from the CURRENT
            # config (each output's own key is its column key). The
            # config is FRESH on purpose so agent edits apply, which
            # means the output set can differ from the one that built
            # these columns: a new output has to become a real column
            # here or its answers land nowhere a surface can read.
            owned = frozenset(columns_for_node(target_list, str(node.id)))
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
            # unreachable from that surface. A fill always owns at least
            # one column, so a resume's set is never empty.
            walked = list(source.column_keys) if source is not None else [column_key]
            until_position = row_count if rows else min(confirmed_row_count or row_count, row_count)
            consented = min(rows, until_position) if rows else until_position
            self._check_budget(resolved_config, consented=consented)
            fill = self._open_fill(
                target_list, node=node, resolved_config=resolved_config, column_keys=column_keys, consented=consented
            )
            scope = WalkScope(
                mode=WalkMode.REMAINING,
                fill_run_id=str(fill.id),
                owed_by=resume_fill_id,
                column_keys=walked,
                until_position=until_position,
                limit=rows,
            )
            probe = processor_for(account_id=self.account_id, node=node, scope=scope).probe(
                target_list, until_position=until_position
            )
            if not probe.found:
                # EMPTY is diagnosed by the probe, which scanned to the
                # end of the range without finding work, so its flag can
                # name WHICH filter emptied it: "the column is done" and
                # "your prompt reads columns these rows have not got"
                # need different next steps from the user.
                if probe.dropped_any:
                    raise NoEligibleRows()
                raise RefillEmpty()
            self._queue_walk(target_list, node=node, scope=scope)

            # The lock, last, over the claim. The guards that read the
            # array run AGAIN here, against the locked copy, because
            # everything above judged an unlocked read; a refusal rolls
            # the fill and its job back with it.
            locked = self._list_or_raise(list_id, lock=True)
            require_fill_column(locked, column_key)
            claim_columns(
                locked,
                config=resolved_config,
                node_id=str(node.id),
                fill_run_id=str(fill.id),
                account_id=self.account_id,
                owned=frozenset(columns_for_node(locked, str(node.id))),
            )
        return fill

    def _open_fill(
        self,
        target_list: List,
        *,
        node: Node,
        resolved_config: AgentConfig,
        column_keys: list[str],
        consented: int,
    ) -> Fill:
        """The fill row, carrying its frozen config and the consented
        count as its denominator; `targeted_at` stays null until the
        walk has queued every run in the range. The node is the one
        binding both callers hold (admit minted it, refill looked it
        up): the fill's agent is read off it. `resolved_config` still
        rides separately, because it is the PROBED config the fill
        freezes (the roster agent's current one, or the quick tab's
        draft), never the node's. NOTHING is written to the sheet: a
        targeted cell shimmers because a queued run says so."""
        return Fill.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
            list_id=str(target_list.id),
            agent_id=agent_id_of(node),
            column_keys=column_keys,
            config_snapshot=resolved_config.model_dump(),
            config_fingerprint=config_fingerprint(resolved_config),
            confirmed_row_count=consented,
            targeted_at=None,
        )

    def _queue_walk(self, target_list: List, *, node: Node, scope: WalkScope) -> None:
        """The job that queues the fill's runs, in this transaction, so
        it can never see a fill that was rolled back."""
        enqueue(self.account_id, EnqueueRuns(list_id=str(target_list.id), node_id=str(node.id), scope=scope))

    @staticmethod
    def _check_budget(config: AgentConfig, *, consented: int) -> None:
        """The free vendor's row cap, refused on the CONSENTED count
        before anything is written: a bound rather than the walked
        count, so a sheet with many rows the prompt cannot act on is
        judged on what the user agreed to spend, not on what the walk
        would have found."""
        if consented > free_provider_row_cap(config):
            raise FreeSearchBudget(searches=MAX_TOOL_CALLS * consented)

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
        # one node, so a second ADMISSION can never reach it.
        agent = self.agents.get(agent_id)
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        return agent, agent.config()

    @staticmethod
    def _require_rows(list_id: str) -> int:
        """The sheet has rows; returns how many, the range a consent
        without an echo covers."""
        row_count = ListRow.objects.filter(list_id=list_id).count()
        if row_count == 0:
            raise EmptyFill()
        return row_count

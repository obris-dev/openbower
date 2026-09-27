"""Fill admission: the one way a user starts a fill, on an AI column
that already exists (created by AiColumnService; the drawer creates then
fills, the tracker fills). It checks the caps and refusals, opens the
fill job, and points the agent's columns at it. It never adds a column:
an agent's output set is fixed while its columns exist.

Admission DECIDES and QUEUES; it does not walk the sheet. The walk
itself (one page per slice, the agent processor judging each row) is
the fill job's first slices, worked within seconds by the jobs
container, so a 50,000 row sheet costs the request only the probe: a
scan that stops at the first row owed work, which reads the whole sheet
only when no row is (a column already tried everywhere, or a prompt no
row can feed). A fill runs the agent's CURRENT config, so an edit
reaches its next row; admission still resolves and probes the config
once, so a broken config refuses at the click. Account-scoped like
every lists service."""

from __future__ import annotations

from django.db import transaction

from agents.services import AgentNotFound, AgentService
from jobs.models import Job
from jobs.services import JobService
from openbower_kernel.fields import new_ulid
from openbower_schema.agents import MAX_TOOL_CALLS, AgentConfig

from ...jobs.fill import FillJob
from ...models import List, ListRow
from ...nodes.registry import ENTRY
from ...processors import processor_for
from ..lists import ListNotFound
from ..workflows import NodeNotFound, WorkflowService, agent_id_of, written_columns
from .base import check_model
from .columns import check_account_cap, check_columns_free, point_columns, require_fill_column
from .errors import (
    ColumnAgentMissing,
    ColumnNoLongerFilled,
    EmptyFill,
    FillColumnDownstream,
    FreeSearchBudget,
    NoEligibleRows,
    NothingToFill,
    ProviderRetiredRefusal,
)
from .targets import free_provider_row_cap


class FillAdmissionService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.agents = AgentService(account_id=account_id)
        self.workflows = WorkflowService(account_id=account_id)

    def fill_column(
        self,
        *,
        list_id: str,
        column_key: str,
        max_row_count: int = 0,
    ) -> Job:
        """A NEW fill over the column, from the drawer right after the
        column is created or from the column's tracker (Fill all
        remaining, Fill next N): the sheet as it stands, or its first N
        owed rows (`max_row_count`). The column names its agent, and the
        fill writes all of that agent's columns as one unit: it targets
        the rows none of them has a record for. A row that ran, whatever
        came of it, is done until the user asks again, and an edited
        prompt changes nothing about that (a fill reads its agent live
        for the rows it does run).

        The shape is judged against UNLOCKED reads and the List lock
        comes last, over the one columns write; the locked pass
        re-runs the guards, and that re-judgement is the one that counts.
        The agent resolve and the model probe run before the transaction
        entirely: network IO must not hold any of it. The row probe runs
        inside it and reads the whole consent when the column is done
        (the NothingToFill answer); a fill writes nothing before it, so
        that scan holds nothing another request waits on."""
        peek = self._list_or_raise(list_id)
        column = require_fill_column(peek, column_key)
        # A fill starts where an arrival does: at an entry action, the
        # action right behind its path's entry marker. A node downstream
        # of a barrier is reached by the workflow, never started. A
        # missing NODE raises as the corruption it is (nodes die only
        # with their list); a missing AGENT is the allowed orphaning.
        front = self.workflows.marker_and_first_action(column.node_id)
        if front is None:
            raise NodeNotFound(column.node_id)
        marker, node = front
        if marker.kind != ENTRY or str(node.id) != column.node_id:
            raise FillColumnDownstream()
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
        check_model(resolved_config)
        with transaction.atomic():
            target_list = self._list_or_raise(list_id)
            require_fill_column(target_list, column_key)
            row_count = self._require_rows(list_id)
            column_keys = self._written_columns(
                target_list, node_id=str(node.id), config=resolved_config, column_key=column_key
            )
            # The deterministic refusals before the queue is built: a
            # "no" knowable up front must not cost a walk first.
            check_columns_free(target_list, column_keys=column_keys)
            check_account_cap(self.account_id)
            fill = self._consent(
                target_list,
                node_id=str(node.id),
                agent_id=str(agent.id),
                column_keys=column_keys,
                row_count=row_count,
                max_row_count=max_row_count,
            )
            self._check_budget(resolved_config, target_row_count=fill.target_row_count)
            processor = processor_for(account_id=self.account_id, node=node)
            probe = processor.probe(target_list, scope=fill.scope(), until_id=fill.until_id, covered=fill.covered)
            if not probe.found:
                # EMPTY is diagnosed by the probe, which scanned to the
                # end of the range without finding work, so its flag can
                # name WHICH filter emptied it: "the column is done" and
                # "your prompt reads columns these rows have not got"
                # need different next steps from the user.
                if probe.dropped_any:
                    raise NoEligibleRows()
                raise NothingToFill()

            # The lock, last, over the consent and the one columns write.
            # Everything above judged an unlocked read, so the column set
            # the fill OWNS is derived again here, from the locked copy,
            # and the job is minted from that: a consent frozen off the
            # unlocked read could name a column deleted meanwhile, whose
            # answers would then land in any column re-added under its
            # key. The guards run again for the same reason.
            locked = self._list_or_raise(list_id, lock=True)
            require_fill_column(locked, column_key)
            column_keys = self._written_columns(
                locked, node_id=str(node.id), config=resolved_config, column_key=column_key
            )
            check_columns_free(locked, column_keys=column_keys)
            check_account_cap(self.account_id)
            consent = self._consent(
                locked,
                node_id=str(node.id),
                agent_id=str(agent.id),
                column_keys=column_keys,
                row_count=row_count,
                max_row_count=max_row_count,
                until_id=fill.until_id,
            )
            job = self._open_fill(locked, consent)
            point_columns(locked, column_keys=column_keys, fill_run_id=str(job.id))
        return job

    @staticmethod
    def _consent(
        target_list: List,
        *,
        node_id: str,
        agent_id: str,
        column_keys: list[str],
        row_count: int,
        max_row_count: int,
        until_id: str = "",
    ) -> FillJob:
        """The fill's consent. The SET is an id minted at the click (ids
        are insertion order, so every row that exists now is older and a
        row appended after is newer and never walked), reused when the
        locked pass rebuilds the consent so the probe and the walk agree
        on the range; the COUNT is the sheet as it stood at the click."""
        return FillJob(
            list_id=str(target_list.id),
            node_id=node_id,
            agent_id=agent_id,
            column_keys=column_keys,
            until_id=until_id or new_ulid(),
            covered=row_count,
            max_row_count=max_row_count,
        )

    @staticmethod
    def _written_columns(target_list: List, *, node_id: str, config: AgentConfig, column_key: str) -> list[str]:
        """The columns this fill writes: the node's, as every lane reads
        them (written_columns), refused when the column asked for is not
        among them."""
        written = list(written_columns(target_list, node_id, config))
        if column_key not in written:
            raise ColumnNoLongerFilled(key=column_key)
        return written

    def _open_fill(self, target_list: List, fill: FillJob) -> Job:
        """The fill job, in this transaction, so it can never see a
        columns write that was rolled back: the consent as its payload,
        the walk as its first slices. NOTHING is written to the sheet:
        a targeted cell shimmers because a queued run says so."""
        return JobService(account_id=self.account_id).enqueue(fill, user_id=self.user_id, target_id=str(target_list.id))

    @staticmethod
    def _check_budget(config: AgentConfig, *, target_row_count: int) -> None:
        """The free vendor's row cap, refused on the fill's target row
        count before anything is written: a bound rather than the walked
        count, so a sheet with many rows the prompt cannot act on is
        judged on what the user asked to spend, not on what the walk
        would have found."""
        if target_row_count > free_provider_row_cap(config):
            raise FreeSearchBudget(searches=MAX_TOOL_CALLS * target_row_count)

    def _list_or_raise(self, list_id: str, *, lock: bool = False) -> List:
        """The account-scoped list read, locked only when the caller is
        about to decide something on it."""
        qs = List.objects.select_for_update() if lock else List.objects
        try:
            return qs.get(id=list_id, account_id=self.account_id)
        except List.DoesNotExist as e:
            raise ListNotFound(list_id) from e

    @staticmethod
    def _require_rows(list_id: str) -> int:
        """The sheet has rows; returns how many, the range the consent
        covers."""
        row_count = ListRow.objects.filter(list_id=list_id).count()
        if row_count == 0:
            raise EmptyFill()
        return row_count

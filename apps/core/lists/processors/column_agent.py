"""The agent kind's processor: which rows a column_agent node owes a
run (three rules, one per walk mode) and how one of its runs executes,
in ONE place.

THE JUDGEMENT.

FRESH (a new fill): a row the prompt can act on, meaning at least one
variable it references renders non-blank (a prompt with no variables
asks the same question everywhere, so every row qualifies). Born READY
under the fill.

REMAINING (a refill): judged across the walked column set, a row is
done only when EVERY column already holds a value (a user's or a prior
fill's, which write-if-blank would refuse); every blank re-runs,
settled or not (the click is the consent to re-spend on a settled
blank, and a fill reads its agent live, so an edited prompt applies
without detection). A resume additionally offers only the rows the
stopped fill still owed (its ABANDONED runs, read rather than
reconstructed). Then the prompt must be able to act on it. Born READY
under the fill.

PUSHED (rows a push appended): the node runs unless the push filled
every column it owns (write-if-blank would keep those values, so the
run would only buy a skip); if ANY is blank the node runs and
write-if-blank protects the sent ones. Born READY, no fill.

Memory is bounded by one page: the owed set is asked per page against
the ids in hand and dropped when the page is done.

THE EXECUTION. A claimed run travels one of three lanes, told apart by
what it carries:

- Preview (`input` set): the drafted config and the hand-fed row ride the
  run itself; it lands its result ON ITSELF (no sheet write, no cell
  truth, no advance), and a config-tier failure settles it unrun.
- Fill-backed (`fill_run_id` set): the fill job's consent names the
  agent and the column set; the agent's config is read LIVE (an edit
  reaches the next row), the claim-time model gate fails the WHOLE
  fill (a config-tier refusal fails every row identically), the row
  comes off the sheet, and the fill job polls its runs for completion.
- Automatic (neither): the row, its list, the node's column set, and
  the agent's config resolved LIVE; a gone row or list settles
  ROW_MISSING or LIST_MISSING, a gone or retired agent settles the run
  unrun, a config-tier failure settles the ONE run.

The tail is shared: the exhausted give-up, the runtime call, the
retriable park, the terminal landing (`land_row`, the one writer of a
resolved row, closing the run inside its own transaction)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from functools import cached_property, partial
from typing import ClassVar, NamedTuple

from pydantic import ValidationError

from agents.providers import ModelUnavailable, model_for
from agents.runtime.prompts import prompt_variables
from agents.services import AgentNotFound, AgentService
from jobs.models import Job
from openbower_schema.agents import AgentConfig
from openbower_schema.fills import CellRunResult

from ..constants import (
    AGENT_MISSING_MESSAGE,
    FILL_RETRY_BACKOFF_SECONDS,
    FILL_SCAN_CHUNK,
    PROVIDER_RETIRED_MESSAGE,
    RETRY_CAUSES,
    FillFailureCode,
    NodeRunStatus,
)
from ..models import List, ListRow, NodeRun
from ..nodes.registry import COLUMN_AGENT
from ..services import fill_progress
from ..services.fill_processing.cell_run import run_cell
from ..services.fill_processing.landing import LandingContext, land_row
from ..services.lists import ListService, RowCursor
from ..services.node_runs import NodeRunFlow
from ..services.runnable import CONFIG_TIER_ERRORS
from ..services.workflows import agent_id_of, columns_for_node
from .base import NodeProcessor, RunOutcome, WalkMode
from .factory import register

logger = logging.getLogger(__name__)


def to_result(run) -> CellRunResult:
    """The run's produced shape, through the contract model, so both
    lanes store the one wire shape."""
    return CellRunResult(
        cells=dict(run.cells),
        evidence=list(run.evidence),
        tool_calls=[o.wire() for o in run.tool_calls],
        assessments=dict(run.assessments),
        declined_cause=run.declined_cause,
        blamed_tool=run.blamed_tool,
        tools=dict(run.tools),
    )


def give_up_blank(task: NodeRun) -> CellRunResult:
    """The exhausted run's blank: it carries the last park's cause AND
    the tool it blamed, so the cell settles with a why instead of
    shimmering forever."""
    prior = CellRunResult(**task.result) if isinstance(task.result, dict) and task.result else CellRunResult()
    return CellRunResult(declined_cause=prior.declined_cause, tools=prior.tools, blamed_tool=prior.blamed_tool)


def _settle_unrun(flow: NodeRunFlow, task: NodeRun) -> None:
    """Settle DONE with no result: the run was skipped (nothing to fill,
    a gone fill or agent, a config-tier refusal), so the cell stays
    never-attempted rather than diagnosed."""
    flow.settle(task.id, status=NodeRunStatus.DONE, result={})


def _park_if_retriable(flow: NodeRunFlow, task: NodeRun, run, result: CellRunResult) -> bool:
    """A fully blank row with a retriable cause (a 429, a timeout):
    park with a real backoff, diagnosing nothing. Returns whether it
    parked, so the caller stops."""
    if not run.cells and run.declined_cause in RETRY_CAUSES:
        flow.park(task.id, backoff_seconds=FILL_RETRY_BACKOFF_SECONDS * task.attempts, result=result.model_dump())
        return True
    return False


class _RunEnded(Exception):
    """A lane builder settled the run before it could execute (its
    subject is gone, its input is unreadable, its fill failed): the
    outcome for the dispatcher, raised as the builder's last act so a
    builder hands back one shape."""

    def __init__(self, outcome: RunOutcome) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class _Lane(NamedTuple):
    """A claimed run's resolved inputs: the config to run, the identity
    its writes land under (None for a run that owns its input and so
    lands on itself), the row it runs on, and the fill job it belongs
    to (None off the fill lane)."""

    config: AgentConfig
    ctx: LandingContext | None
    row_data: dict
    fill_run_id: str | None


def row_is_eligible(data: dict, variables: set[str]) -> bool:
    """Whether the prompt can ACT on this row: at least one referenced
    variable renders non-blank. ONE definition for every walk."""
    if not variables:
        return True
    return any(str(data.get(variable, "")).strip() for variable in variables)


class Probe(NamedTuple):
    """What a scan for the FIRST qualifying row found: whether one
    exists, and whether any row was dropped because the prompt could
    not act on it (meaningful only when none was found, where "the
    column is done" and "your prompt reads columns these rows have not
    got" need different next steps)."""

    found: bool
    dropped_any: bool


class AIColumnProcessor(NodeProcessor):
    KIND: ClassVar[str] = COLUMN_AGENT

    @cached_property
    def variables(self) -> set[str]:
        """The prompt's variables, off the node's agent as it is NOW: a
        fill reads its agent live, so the judgement does too."""
        agent = AgentService(account_id=self.account_id).get_for_fill(agent_id_of(self.node))
        return prompt_variables(agent.config().prompt)

    def judged_keys(self, target_list: List) -> list[str]:
        """The columns a REMAINING walk judges owed-ness across: the
        walk's own set, else every column this node fills here."""
        return list(self.scope.column_keys) or columns_for_node(target_list, str(self.node.id))

    def enqueue_runs(self, target_list: List, rows: Sequence[ListRow], *, now: datetime, limit: int = 0) -> int:
        if not rows:
            return 0
        facts = self._page_facts(rows)
        owed: list[ListRow] = []
        for row in rows:
            if not self._qualifies(target_list, row, facts):
                continue
            owed.append(row)
            if limit and len(owed) == limit:
                # The limit is reached: the rest of the page is not judged.
                break
        if not owed:
            return 0
        fill_run_id = self.scope.fill_run_id or None
        runs = [
            NodeRun(
                account_id=self.account_id,
                fill_run_id=fill_run_id,
                node_id=str(self.node.id),
                kind=COLUMN_AGENT,
                row_id=str(row.id),
                list_id=str(target_list.id),
                rank=row.rank,
                status=NodeRunStatus.READY,
                last_state_change_at=now,
            )
            for row in owed
        ]
        NodeRun.objects.bulk_create(runs, ignore_conflicts=True)
        return len(runs)

    def probe(self, target_list: List, *, until_id: str = "", covered: int = 0) -> Probe:
        """Scan for the FIRST row this walk would queue, in sheet order
        within the consent (the set, `until_id`, and the count,
        `covered`; "" and 0 mean unbounded), without queuing anything:
        admission's zero check. Pages exactly as the walk does, bounded
        exactly as the walk is, and stops at the first hit, so a sheet
        with work near the top costs one page and a hit past what the
        walk may cover is not a hit."""
        lists = ListService(account_id=self.account_id)
        after: RowCursor | None = None
        dropped_any = False
        walked = 0
        while True:
            remaining = covered - walked if covered else FILL_SCAN_CHUNK
            if remaining <= 0:
                return Probe(found=False, dropped_any=dropped_any)
            page = lists.rows_page(target_list, after=after, limit=min(FILL_SCAN_CHUNK, remaining), until_id=until_id)
            if not page:
                return Probe(found=False, dropped_any=dropped_any)
            walked += len(page)
            facts = self._page_facts(page)
            for row in page:
                verdict = self._judge(target_list, row, facts)
                if verdict is _Verdict.OWED:
                    return Probe(found=True, dropped_any=dropped_any)
                if verdict is _Verdict.DROPPED:
                    dropped_any = True
            after = RowCursor(str(page[-1].id), page[-1].rank)

    # The execution.

    def process_run(self, task: NodeRun, *, flow: NodeRunFlow) -> RunOutcome:
        try:
            lane = self._lane(task, flow=flow)
        except _RunEnded as ended:
            return ended.outcome
        close = partial(flow.settle, task.id, status=NodeRunStatus.DONE)
        if flow.exhausted(task):
            self._land(task, lane, give_up_blank(task), close=close, flow=flow)
            return RunOutcome.DONE
        try:
            run = run_cell(lane.config, lane.row_data)
        except CONFIG_TIER_ERRORS as e:
            # Config-tier: the agent cannot run at all (no model, a
            # retired tool, which surfaces only here since model_for
            # does not resolve tools). It fails every row identically,
            # so retrying buys nothing: the fill fails loudly, the
            # automatic run and the preview settle and move on.
            if lane.fill_run_id is not None:
                fill_progress.fail(lane.fill_run_id, code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            else:
                lane_name = "preview" if task.is_preview else "autofill"
                logger.warning("%s: node %s unrunnable (%s); settling task %s", lane_name, task.node_id, e, task.id)
            _settle_unrun(flow, task)
            return RunOutcome.DONE
        result = to_result(run)
        if _park_if_retriable(flow, task, run, result):
            return RunOutcome.PARKED
        self._land(task, lane, result, close=close, flow=flow)
        return RunOutcome.DONE

    def _lane(self, task: NodeRun, *, flow: NodeRunFlow) -> _Lane:
        """Which of the three lanes this run is on, by what the run
        carries (its own input, a fill, or a row alone), and that lane's
        config, row, and landing context. A builder that settles the run
        instead raises _RunEnded with the outcome."""
        if task.is_preview:
            return self._preview_lane(task, flow=flow)
        if task.fill_run_id:
            return self._fill_lane(task, flow=flow)
        return self._live_lane(task, flow=flow)

    @staticmethod
    def _preview_lane(task: NodeRun, *, flow: NodeRunFlow) -> _Lane:
        try:
            config = AgentConfig(**task.input["config"])
            row_data = task.input["row"]
        except (KeyError, TypeError, ValidationError) as e:
            # An input this build cannot read: settled unrun once, not
            # parked to the attempt cap with the builder's button busy.
            logger.warning("preview: run %s carries an unreadable input (%s); settling unrun", task.id, e)
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE) from e
        # No landing context: a run that owns its input lands on itself.
        return _Lane(config=config, ctx=None, row_data=row_data, fill_run_id=None)

    def _fill_lane(self, task: NodeRun, *, flow: NodeRunFlow) -> _Lane:
        from ..jobs.fill import FillJob

        job = Job.objects.filter(id=task.fill_run_id, kind=FillJob.KIND).first()
        if job is None:
            # The owning fill is gone (its list was deleted, which purges
            # both in one transaction); nothing to run or land.
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE)
        consent = FillJob.model_validate(job.payload)
        try:
            agent = AgentService(account_id=task.account_id).get_for_fill(consent.agent_id)
        except AgentNotFound:
            # The agent was deleted mid-fill (an ephemeral one dies with
            # its last column): a config-tier fact, so the WHOLE fill
            # fails and says so; this task settles unrun, the cell
            # stays never-attempted.
            fill_progress.fail(str(job.id), code=FillFailureCode.AGENT_MISSING, message=AGENT_MISSING_MESSAGE)
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE) from None
        if agent.provider_retired:
            # Acting on a substituted spec would be a guess: the same
            # fact admission refuses under, caught here mid-fill.
            fill_progress.fail(str(job.id), code=FillFailureCode.PROVIDER_RETIRED, message=PROVIDER_RETIRED_MESSAGE)
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE)
        config = agent.config()
        # Claim-time model resolution is AUTHORITATIVE (a stale reclaim
        # hours later re-resolves against the current world).
        try:
            model_for(config.provider, config.source, config.model)
        except ModelUnavailable as e:
            fill_progress.fail(str(job.id), code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE) from e
        row = ListRow.objects.filter(id=task.row_id, list_id=consent.list_id).first()
        if row is None:
            if not List.objects.filter(id=consent.list_id).exists():
                # The whole list went away mid-walk: a user deletion is
                # CANCELLED, never a failure story.
                fill_progress.cancel(str(job.id))
            flow.settle(task.id, status=NodeRunStatus.ROW_MISSING, result={})
            raise _RunEnded(RunOutcome.ROW_MISSING)
        ctx = LandingContext(
            account_id=task.account_id,
            list_id=consent.list_id,
            column_keys=tuple(consent.column_keys),
            fill_run_id=str(job.id),
            node_id=task.node_id,
        )
        return _Lane(config=config, ctx=ctx, row_data=row.data, fill_run_id=str(job.id))

    def _live_lane(self, task: NodeRun, *, flow: NodeRunFlow) -> _Lane:
        row = ListRow.objects.filter(id=task.row_id).first()
        if row is None:
            flow.settle(task.id, status=NodeRunStatus.ROW_MISSING, result={})
            raise _RunEnded(RunOutcome.ROW_MISSING)
        target_list = List.objects.filter(id=row.list_id, account_id=task.account_id).first()
        if target_list is None:
            flow.settle(task.id, status=NodeRunStatus.LIST_MISSING, result={})
            raise _RunEnded(RunOutcome.LIST_MISSING)
        # The node's column set, resolved live: empty means the node no
        # longer fills any column here (its columns were removed), so
        # there is nothing to run; settle so the run does not linger.
        column_keys = columns_for_node(target_list, task.node_id)
        if not column_keys:
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE)
        try:
            agent = AgentService(account_id=task.account_id).get_for_fill(agent_id_of(self.node))
        except AgentNotFound:
            # An agent delete leaves its columns orphaned on purpose:
            # the allowed shape, so no warning.
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE) from None
        if agent.provider_retired:
            # A retired provider cannot run its stored config; settle
            # rather than burn attempts on a run that will never
            # succeed. The cell stays never-attempted, targetable later.
            logger.warning("autofill: node %s provider retired; settling task %s unrun", task.node_id, task.id)
            _settle_unrun(flow, task)
            raise _RunEnded(RunOutcome.DONE)
        config = agent.config()
        ctx = LandingContext(
            account_id=task.account_id,
            list_id=str(target_list.id),
            column_keys=column_keys,
            fill_run_id=None,
            node_id=task.node_id,
        )
        return _Lane(config=config, ctx=ctx, row_data=row.data, fill_run_id=None)

    def _land(self, task: NodeRun, lane: _Lane, payload: CellRunResult, *, close, flow: NodeRunFlow) -> None:
        if lane.ctx is None:
            # A run that owns its input lands ON ITSELF: no sheet write,
            # no cell truth, no advance (there is no sheet at all).
            flow.settle(task.id, payload.model_dump(), status=NodeRunStatus.DONE)
            return
        land_row(lane.ctx, task.row_id, payload, close=close)

    # The judgement.

    def _qualifies(self, target_list: List, row: ListRow, facts: _PageFacts) -> bool:
        return self._judge(target_list, row, facts) is _Verdict.OWED

    def _judge(self, target_list: List, row: ListRow, facts: _PageFacts) -> _Verdict:
        mode = self.scope.mode
        if mode is WalkMode.PUSHED:
            keys = columns_for_node(target_list, str(self.node.id))
            if keys and all((row.data.get(key) or "").strip() for key in keys):
                return _Verdict.DONE
            return _Verdict.OWED
        if mode is WalkMode.REMAINING:
            row_id = str(row.id)
            # The resume bound goes FIRST: a row the stopped fill never
            # consented to is not its remaining work, and counting it as
            # dropped would blame the prompt for a row the scope excluded.
            if facts.owed is not None and row_id not in facts.owed:
                return _Verdict.DONE
            if all(str(row.data.get(key, "") or "").strip() for key in self.judged_keys(target_list)):
                return _Verdict.DONE
        if mode in (WalkMode.FRESH, WalkMode.REMAINING):
            return _Verdict.OWED if row_is_eligible(row.data, self.variables) else _Verdict.DROPPED
        # A structural backfill has no meaning for an agent node.
        return _Verdict.DONE

    def _page_facts(self, rows: Sequence[ListRow]) -> _PageFacts:
        """The one membership set a REMAINING walk asks per page: which
        of these rows the resumed fill still owed."""
        if self.scope.mode is not WalkMode.REMAINING or not self.scope.owed_by:
            return _PageFacts(owed=None)
        ids = [str(row.id) for row in rows]
        owed = {
            str(row_id)
            for row_id in NodeRun.objects.filter(
                account_id=self.account_id,
                fill_run_id=self.scope.owed_by,
                status=NodeRunStatus.ABANDONED,
                row_id__in=ids,
            ).values_list("row_id", flat=True)
        }
        return _PageFacts(owed=owed)


class _Verdict(StrEnum):
    OWED = "owed"
    DONE = "done"
    # An owed row the prompt cannot act on.
    DROPPED = "dropped"


class _PageFacts(NamedTuple):
    owed: set[str] | None


register(AIColumnProcessor)

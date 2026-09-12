"""The fill-task PROCESSOR: given a CLAIMED task, run its agent and land
the result. A trusted-process service (like fill_progress / fill_tasks),
resolving by id; the consume operation is only the loop that feeds it.

Two lanes, one shape:

- AutofillTask (`fill_run_id` NULL): resolves the row's list, the agent's
  config, and the agent's column set LIVE. A gone row or list settles
  ROW_MISSING / LIST_MISSING; a config-tier failure settles the ONE task.

- FillBackedTask (`fill_run_id` set): rebuilds the fill's FROZEN config and
  landing context, runs the claim-time model gate (a config-tier refusal
  FAILS the whole fill), lands on the sheet (NORMAL) or the task (TEST),
  and nudges completion after each settle.

The genuinely shared bits, the retriable-park branch, the exhausted-blank,
and the terminal landing close, live on the base so the two lanes cannot
drift apart. `flow` and the frozen lane's context are cached properties,
derived from the primitives, never threaded in.
"""

from __future__ import annotations

import logging
from functools import cached_property, partial

from agents.providers import ModelUnavailable, model_for
from agents.services import AgentNotFound, AgentService
from agents.tools import registry as tool_registry
from openbower_schema.agents import AgentConfig
from openbower_schema.fills import CellRunResult

from ...constants import (
    FILL_RETRY_BACKOFF_SECONDS,
    RETRY_CAUSES,
    FillFailureCode,
    FillKind,
    FillStatus,
    FillTaskStatus,
)
from ...models import Fill, List, ListRow
from .. import fill_progress
from ..fill_tasks import FillTaskFlow
from ..fingerprint import config_fingerprint
from .cell_run import run_cell
from .landing import LandingContext, land_row

logger = logging.getLogger(__name__)


def to_result(run) -> CellRunResult:
    """The run's produced shape, through the contract model, so both lanes
    store the one wire shape."""
    return CellRunResult(
        cells=dict(run.cells),
        evidence=list(run.evidence),
        tool_calls=[o.wire() for o in run.tool_calls],
        assessments=dict(run.assessments),
        declined_cause=run.declined_cause,
        blamed_tool=run.blamed_tool,
        tools=dict(run.tools),
    )


class ProcessFillTask:
    """One claimed task's run. Built from the primitives (the task and the
    worker id); `flow` is a cached property, not a threaded-in instance."""

    def __init__(self, *, task, worker_id: str) -> None:
        self.task = task
        self.worker_id = worker_id

    @cached_property
    def flow(self) -> FillTaskFlow:
        return FillTaskFlow(worker_id=self.worker_id)

    def _settle_done(self) -> None:
        """Settle DONE with no result: the run was skipped (nothing to
        fill, a gone agent, a config-tier refusal), so the cell stays
        never-attempted rather than diagnosed."""
        self.flow.settle(self.task.id, status=FillTaskStatus.DONE, result={})

    def _close(self):
        """land_row's terminal callback: settle the task DONE inside the
        landing's own transaction, so a landing miss rolls both back."""
        return partial(self.flow.settle, self.task.id, status=FillTaskStatus.DONE)

    def _park_if_retriable(self, run, result: CellRunResult) -> bool:
        """A fully blank row with a retriable cause (a 429, a timeout):
        park with a real backoff, diagnosing nothing. Returns whether it
        parked, so the caller stops."""
        if not run.cells and run.declined_cause in RETRY_CAUSES:
            self.flow.park(
                self.task.id,
                backoff_seconds=FILL_RETRY_BACKOFF_SECONDS * self.task.attempts,
                result=result.model_dump(),
            )
            return True
        return False

    def _give_up_blank(self) -> CellRunResult:
        """The exhausted task's blank: it carries the last park's cause AND
        the tool it blamed, so the cell settles with a why instead of
        shimmering forever."""
        prior = (
            CellRunResult(**self.task.result)
            if isinstance(self.task.result, dict) and self.task.result
            else CellRunResult()
        )
        return CellRunResult(declined_cause=prior.declined_cause, tools=prior.tools, blamed_tool=prior.blamed_tool)

    def finish(self) -> None:
        """Completion nudge after a settle. Autofill has no fill to finish;
        the fill lane overrides."""


class AutofillTask(ProcessFillTask):
    """A null-run task: everything resolved LIVE off the sheet, landing on
    the row, a config-tier failure settling only this task."""

    def process(self) -> str:
        task = self.task
        row = ListRow.objects.filter(id=task.row_id).first()
        if row is None:
            self.flow.settle(task.id, status=FillTaskStatus.ROW_MISSING, result={})
            return "row_missing"
        target_list = List.objects.filter(id=row.list_id, account_id=task.account_id).first()
        if target_list is None:
            self.flow.settle(task.id, status=FillTaskStatus.LIST_MISSING, result={})
            return "list_missing"
        # The agent's column set, resolved live: empty means the agent no
        # longer fills any column here (removed or reassigned), so there is
        # nothing to run; settle so the task does not linger.
        column_keys = tuple(
            column["key"]
            for column in target_list.columns
            if column.get("fill") and column["fill"].get("agent_id") == task.agent_id
        )
        if not column_keys:
            self._settle_done()
            return "done"
        try:
            agent = AgentService(account_id=task.account_id).get_for_fill(task.agent_id)
        except AgentNotFound:
            self._settle_done()
            return "done"
        if agent.provider_retired:
            # A retired provider cannot run its stored config; settle rather
            # than burn attempts on a run that will never succeed. The cell
            # stays never-attempted, targetable later.
            logger.warning("autofill: agent %s provider retired; settling task %s unrun", task.agent_id, task.id)
            self._settle_done()
            return "done"
        config = agent.config()
        ctx = LandingContext(
            account_id=task.account_id,
            list_id=str(target_list.id),
            column_keys=column_keys,
            fill_run_id=None,
            config_fingerprint=config_fingerprint(config),
        )
        if self.flow.exhausted(task):
            land_row(ctx, task.row_id, self._give_up_blank(), close=self._close())
            return "done"
        try:
            run = run_cell(config, row.data)
        except (ModelUnavailable, tool_registry.UnknownTool) as e:
            # Config-tier: the agent cannot run at all (no model, an unknown
            # tool). It fails every row identically, so retrying buys
            # nothing; settle and move on.
            logger.warning("autofill: agent %s unrunnable (%s); settling task %s", task.agent_id, e, task.id)
            self._settle_done()
            return "done"
        result = to_result(run)
        if self._park_if_retriable(run, result):
            return "parked"
        land_row(ctx, task.row_id, result, close=self._close())
        return "done"


class FillBackedTask(ProcessFillTask):
    """A fill-backed task: the SAME run as autofill, but with the fill's
    FROZEN config and column set (mid-fill agent edits never apply). A TEST
    fill lands on its task; a NORMAL fill lands on its sheet row.
    Completion is nudged after each settle."""

    @cached_property
    def fill(self) -> Fill | None:
        return Fill.objects.filter(id=self.task.fill_run_id).first()

    @cached_property
    def config(self) -> AgentConfig:
        return AgentConfig(**self.fill.config_snapshot)

    @cached_property
    def ctx(self) -> LandingContext:
        return LandingContext.from_fill(self.fill)

    def finish(self) -> None:
        fill_progress.try_finish(str(self.fill.id))

    def _land(self, payload: CellRunResult) -> None:
        if self.fill.kind == FillKind.TEST:
            # A test run lands ON ITS TASK: no sheet write, no cell truth
            # (there may be no sheet at all).
            self.flow.settle(self.task.id, payload.model_dump(), status=FillTaskStatus.DONE)
        else:
            land_row(self.ctx, self.task.row_id, payload, close=self._close())

    def process(self) -> str:
        if self.fill is None:
            # The owning fill is gone (its list was deleted, which purges
            # both in one transaction); nothing to run or land.
            self._settle_done()
            return "done"
        # RUNNING on first claim: a live fill with a row in flight is
        # running. CAS from PENDING so it is a cheap no-op once flipped.
        Fill.objects.filter(id=self.fill.id, status=FillStatus.PENDING).update(status=FillStatus.RUNNING)
        # Claim-time model resolution is AUTHORITATIVE (a stale reclaim
        # hours later re-resolves against the current world). A config-tier
        # refusal fails EVERY row identically, so it fails the whole fill
        # loudly rather than burning attempts; this is the surviving
        # config-tier FAILED writer.
        try:
            model_for(self.config.provider, self.config.source, self.config.model)
        except (ModelUnavailable, tool_registry.UnknownTool) as e:
            fill_progress.fail(str(self.fill.id), code=FillFailureCode.MODEL_UNRUNNABLE, message=str(e))
            self._settle_done()
            return "done"
        if self.flow.exhausted(self.task):
            self._land(self._give_up_blank())
            self.finish()
            return "done"
        if self.fill.kind == FillKind.TEST:
            # A test run rides the fill's own row_data (position-indexed),
            # never a sheet.
            row_data = self.fill.row_data[self.task.position]
        else:
            row = ListRow.objects.filter(id=self.task.row_id, list_id=self.fill.list_id).first()
            if row is None:
                if not List.objects.filter(id=self.fill.list_id).exists():
                    # The whole list went away mid-walk: a user deletion is
                    # CANCELLED, never a failure story.
                    fill_progress.cancel(str(self.fill.id))
                self.flow.settle(self.task.id, status=FillTaskStatus.ROW_MISSING, result={})
                self.finish()
                return "row_missing"
            row_data = row.data
        run = run_cell(self.config, row_data)
        result = to_result(run)
        if self._park_if_retriable(run, result):
            return "parked"
        self._land(result)
        self.finish()
        return "done"

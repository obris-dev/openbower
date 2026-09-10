"""The autofill worker's LOOP: drain the automatic path off the unified
task spine. The process (signals, lifecycle) lives in the management
command; this is the drain it drives, beside the fill worker's
supervisor.

An autofill task has NO Fill, so the worker is self-describing off the
task: given a null-run task it resolves the row's list, the agent's
config, and the agent's column set LIVE, runs the agent once, and lands
the result with no fill run. It reuses the queue's claim + settle seams
and landing.py, so the automatic path and a user fill land a cell the
same way. Transient failures park and retry (the queue's backoff); a run
at the attempt cap gives up with a blank; a gone row or list settles
terminally (ROW_MISSING / LIST_MISSING).

Single-threaded for now: it claims a batch and runs it in order. There
is one autofill worker, so a lease that goes stale mid-batch is never
reclaimed out from under it. Per-row concurrency and per-list fairness
are a later refinement.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from django.db import DatabaseError

from agents.providers import ModelUnavailable
from agents.services import AgentNotFound, AgentService
from agents.tools import registry as tool_registry
from openbower_schema.fills import CellRunResult

from ..constants import AUTOFILL_WORKER_IDLE_SECONDS, FILL_CLAIM_BATCH, FILL_RETRY_BACKOFF_SECONDS, RETRY_CAUSES
from ..models import List, ListRow
from ..services.cell_run import run_cell
from ..services.fill_queue import FillQueueService
from ..services.fingerprint import config_fingerprint
from ..services.landing import LandingContext, land_row
from ..services.lists import ListNotFound, RowNotFound

logger = logging.getLogger(__name__)

# Liveness marker the loop refreshes every pass; the compose healthcheck
# marks the worker unhealthy when it goes stale, catching a WEDGED loop
# without false-alarming on an empty queue (an idle worker still loops
# and touches this). A fixed path so the healthcheck can name it.
_HEARTBEAT_PATH = Path("/tmp/autofill_worker.heartbeat")


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError as e:
        logger.warning("autofill heartbeat write failed: %s", e)


def _to_result(run) -> CellRunResult:
    """The run's produced shape, through the contract model, so the
    automatic path and the fill worker store the one wire shape."""
    return CellRunResult(
        cells=dict(run.cells),
        evidence=list(run.evidence),
        tool_calls=[o.wire() for o in run.tool_calls],
        assessments=dict(run.assessments),
        declined_cause=run.declined_cause,
        blamed_tool=run.blamed_tool,
        tools=dict(run.tools),
    )


class AutofillWorkerOperation:
    """The drain loop. One instance per worker process; state is the
    queue, so a restart loses nothing but in-flight progress (the next
    claim re-derives it from the tasks that survive)."""

    def __init__(self, *, worker_id: str, stop) -> None:
        self.worker_id = worker_id
        self.stop = stop
        self.queue = FillQueueService(worker_id=worker_id)

    def run(self, once: bool = False) -> None:
        while not self.stop.is_set():
            _touch_heartbeat()  # the loop is turning, empty queue or not
            try:
                claimed = self.queue.claim_autofill_batch(free_slots=FILL_CLAIM_BATCH)
            except DatabaseError as e:
                # Transient (a restart mid-connection, a lock timeout):
                # idle and retry rather than crash, like the fill worker.
                logger.warning("autofill claim hit a database error, retrying: %s", e)
                self.stop.wait(AUTOFILL_WORKER_IDLE_SECONDS)
                continue
            if not claimed:
                if once:
                    break
                self.stop.wait(AUTOFILL_WORKER_IDLE_SECONDS)  # SIGTERM wakes it
                continue
            for task in claimed:
                if self.stop.is_set():
                    # Drain: hand the unrun task back so the next start
                    # (or a rescan) takes it; its attempt is already counted.
                    self.queue.release_lease(task)
                    continue
                self._process(task)

    def _process(self, task) -> None:
        try:
            self._run(task)
        except (ListNotFound, RowNotFound):
            # The row or list vanished between resolve and land (a user
            # deletion): terminal, nothing to diagnose.
            self.queue.mark_row_missing(task)

    def _run(self, task) -> None:
        row = ListRow.objects.filter(id=task.row_id).first()
        if row is None:
            self.queue.mark_row_missing(task)
            return
        target = List.objects.filter(id=row.list_id, account_id=task.account_id).first()
        if target is None:
            self.queue.mark_list_missing(task)
            return
        # The agent's column set, resolved live: the sheet's AI columns
        # this task's agent fills. Empty means the agent no longer fills
        # any column here (removed or reassigned), so there is nothing to
        # run; settle so the task does not linger.
        column_keys = tuple(
            column["key"]
            for column in target.columns
            if column.get("fill") and column["fill"].get("agent_id") == task.agent_id
        )
        if not column_keys:
            self.queue.complete_task(task, {})
            return
        try:
            agent = AgentService(account_id=task.account_id, user_id=target.user_id).get_for_fill(task.agent_id)
        except AgentNotFound:
            self.queue.complete_task(task, {})
            return
        if agent.provider_retired:
            # A retired provider cannot run its stored config; settle the
            # task rather than burn attempts on a run that will never
            # succeed. The cell stays never-attempted, targetable later.
            logger.warning("autofill: agent %s provider retired; settling task %s unrun", task.agent_id, task.id)
            self.queue.complete_task(task, {})
            return
        config = agent.config()
        ctx = LandingContext(
            account_id=task.account_id,
            user_id=target.user_id,
            list_id=str(target.id),
            column_keys=column_keys,
            fill_run_id=None,
            config_fingerprint=config_fingerprint(config),
        )
        if FillQueueService.exhausted(task):
            self._give_up(task, ctx)
            return
        try:
            run = run_cell(config, row.data)
        except (ModelUnavailable, tool_registry.UnknownTool) as e:
            # Config-tier: the agent cannot run at all (no model, an
            # unknown tool). It fails every row identically, so retrying
            # buys nothing; settle and move on.
            logger.warning("autofill: agent %s unrunnable (%s); settling task %s", task.agent_id, e, task.id)
            self.queue.complete_task(task, {})
            return
        result = _to_result(run)
        if not run.cells and run.declined_cause in RETRY_CAUSES:
            # A fully blank row with a retriable cause (a 429, a timeout):
            # park and come round after a real backoff, diagnosing nothing.
            self.queue.park_task(
                task,
                backoff_seconds=FILL_RETRY_BACKOFF_SECONDS * task.attempts,
                result=result.model_dump(),
            )
            return
        land_row(ctx, task.row_id, result, close=partial(self.queue.complete_task, task))

    def _give_up(self, task, ctx: LandingContext) -> None:
        """The task exhausted its retries: land a blank carrying the last
        park's cause, so its cells settle with a why instead of shimmering
        forever."""
        prior = CellRunResult(**task.result) if isinstance(task.result, dict) and task.result else CellRunResult()
        blank = CellRunResult(declined_cause=prior.declined_cause, tools=prior.tools)
        land_row(ctx, task.row_id, blank, close=partial(self.queue.complete_task, task))

"""Bench runs: the agent builder's one-row diagnostic as ONE NodeRun
that owns its input. No Fill, no sheet, no cell truth: the drafted
config and the hand-fed row ride the run's `input`, the run is a run
of the account's bench node on the automatic lane (the autofill
provisioner publishes it, the autofill consumer runs it), its result
lands on itself, and a poll reads it back by id. The throwaway rides
the real execution path on purpose: every bench click regression-tests
the machinery fills depend on.

One live bench run per account, held ADVISORILY: your own live run is
superseded by your next click; a teammate's fresh one refuses; a stale
orphan (a tab that died mid-run) is superseded whoever started it.

Account-scoped like every lists service; the age prune is the cron's
(operations/prune_bench_runs.py), global."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from agents.providers import ModelUnavailable, model_for
from agents.tools import registry as tool_registry
from agents.tools.registry import UnknownTool
from openbower_schema.agents import (
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_VALUE_MAX_LENGTH,
    AgentConfig,
)
from openbower_schema.fills import CellRunResult

from ..constants import NON_TERMINAL_NODE_RUN_STATES, BenchErrorCode, NodeRunStatus
from ..models import NodeRun
from ..nodes.registry import COLUMN_AGENT
from .node_runs import PROCESSING_STALE_SECONDS
from .workflows import WorkflowService


class BenchRefused(Exception):
    """A bench start the server refuses: `code` is the machine leg of
    the {error, detail} envelope, the message is copy rendered
    verbatim."""

    code: BenchErrorCode = BenchErrorCode.TEST_ROW_INVALID


class BenchRowInvalid(BenchRefused):
    """A hand-fed row past the wire's bench bounds: REFUSED, never
    truncated, because a truncated test would diagnose a different row
    than the user typed. The bounds ship in x-constants, so a client
    can make this refusal unreachable; the copy names the bound that
    fired."""

    code = BenchErrorCode.TEST_ROW_INVALID


class BenchActive(BenchRefused):
    """A TEAMMATE'S bench run is observably live (a fresh state change
    within the processing window). Your own live run never refuses: it
    is superseded by the new start."""

    code = BenchErrorCode.TEST_ACTIVE

    def __init__(self) -> None:
        super().__init__("A teammate's test is running; wait a moment for it to finish.")


class BenchUnrunnable(BenchRefused):
    """The drafted config cannot run at all (no model, an unknown
    tool): refused before a run exists, so a bench click never starts
    a run that crashes on its one row."""

    code = BenchErrorCode.MODEL_UNRUNNABLE


class BenchRunNotFound(Exception):
    """Missing OR foreign run (cross-tenant reads as not-found), or a
    run that is not a bench run at all."""


def bench_runs() -> QuerySet[NodeRun]:
    """Every bench run, any account: the agent runs that carry their
    own input. Structural (no row, no fill, of the agent kind), so no
    flag can drift from it."""
    return NodeRun.objects.filter(kind=COLUMN_AGENT, fill_run_id__isnull=True, row_id__isnull=True)


class BenchRunService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def start(self, *, config: AgentConfig, row: dict) -> NodeRun:
        """One READY bench run of the account's bench node, carrying the
        drafted config and the row. The row's bounds and the config's
        runnability refuse BEFORE anything is written; the supersede
        scan runs unlocked (the worker's terminal CAS makes a double
        abandon a no-op, and two simultaneous starts that both pass it
        leave two live runs until the next click supersedes both, which
        an advisory bound tolerates)."""
        self._check_row(row)
        self._check_config(config)
        self._supersede_or_refuse()
        now = timezone.now()
        with transaction.atomic():
            # The bench node: the account's one sheetless column_agent
            # node, so the run has a node to be a run of. The drafted
            # config rides the run, never the node.
            bench = WorkflowService(account_id=self.account_id).get_or_create_bench_node()
            return NodeRun.objects.create(
                account_id=self.account_id,
                fill_run_id=None,
                node_id=str(bench.id),
                kind=bench.kind,
                row_id=None,
                list_id="",
                position=0,
                status=NodeRunStatus.READY,
                input={"row": row, "config": config.model_dump()},
                started_by=self.user_id,
                last_state_change_at=now,
            )

    def get(self, run_id: str) -> NodeRun:
        run = bench_runs().filter(id=run_id, account_id=self.account_id).first()
        if run is None:
            raise BenchRunNotFound(run_id)
        return run

    def cancel(self, run_id: str) -> NodeRun:
        """Abandon a bench run that has not been claimed (READY or
        QUEUED). A run a consumer already owns runs to its own terminal
        CAS: in-flight spend is sunk cost, and its result still lands
        (a cancel racing the landing must not strand a paid diagnosis).
        A run already terminal is a no-op, not an error."""
        self.get(run_id)
        _abandon([run_id])
        return self.get(run_id)

    @staticmethod
    def result(run: NodeRun) -> CellRunResult | None:
        """The run's stored result, read back through the contract model
        it was written through; None until the run finished with one."""
        if run.status != NodeRunStatus.DONE or not run.result:
            return None
        return CellRunResult.model_validate(run.result)

    @staticmethod
    def _check_row(row: dict) -> None:
        """The bench bounds, refused HERE so the refusal rides the
        {error, detail} envelope every other refusal uses (a
        serializer-raised bound answers DRF's field shape, which the
        client cannot read). First, before the model gate: a row the
        wire refuses needs no probe."""
        if len(row) > TEST_ROW_MAX_KEYS:
            raise BenchRowInvalid(f"a test row carries at most {TEST_ROW_MAX_KEYS} values")
        if any(len(key) > TEST_KEY_MAX_LENGTH for key in row):
            raise BenchRowInvalid(f"row keys are bounded at {TEST_KEY_MAX_LENGTH} characters")
        if any(len(value) > TEST_VALUE_MAX_LENGTH for value in row.values()):
            raise BenchRowInvalid(f"row values are bounded at {TEST_VALUE_MAX_LENGTH} characters")

    @staticmethod
    def _check_config(config: AgentConfig) -> None:
        """Start-time UX only; the worker's claim-time resolution is
        authoritative. The toggles resolve here too: a toggle naming no
        registered tool fails the row identically."""
        try:
            model_for(config.provider, config.source, config.model)
            tool_registry.toggled_tools(config)
        except (ModelUnavailable, UnknownTool) as e:
            raise BenchUnrunnable(str(e)) from e

    def _supersede_or_refuse(self) -> None:
        """Liveness is the run's latest state change within
        PROCESSING_STALE_SECONDS (the reclaim's dead bound, not the
        tighter lease window: a single run's stamp freezes while its
        bounded run_cell runs, and must not read stale mid-run). A
        FRESH teammate's run refuses; everything else (your own, or
        anyone's stale orphan) is abandoned and superseded."""
        fresh_cutoff = timezone.now() - timedelta(seconds=PROCESSING_STALE_SECONDS)
        live = list(
            bench_runs()
            .filter(account_id=self.account_id, status__in=NON_TERMINAL_NODE_RUN_STATES)
            .only("id", "started_by", "last_state_change_at")
        )
        for run in live:
            if run.started_by != self.user_id and run.last_state_change_at >= fresh_cutoff:
                raise BenchActive()
        _abandon([str(run.id) for run in live])


def _abandon(run_ids: list[str]) -> int:
    """READY | QUEUED -> ABANDONED, deliberately NOT PROCESSING (the
    fill lane's abandon rule: a claimed run runs to its own CAS)."""
    if not run_ids:
        return 0
    return NodeRun.objects.filter(id__in=run_ids, status__in=(NodeRunStatus.READY, NodeRunStatus.QUEUED)).update(
        status=NodeRunStatus.ABANDONED, last_state_change_at=timezone.now(), updated_at=timezone.now()
    )

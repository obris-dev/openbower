"""Preview runs: the agent builder's one-row diagnostic as ONE NodeRun
that owns its input. No Fill, no sheet, no cell truth: the drafted
config and the hand-fed row ride the run's `input`, the run is a run
of the account's preview node on its own lane (the autofill provisioner
picks it and routes it to the preview topic; the preview consumer runs
it), its result
lands on itself, and a poll reads it back by id. The throwaway rides
the real execution path on purpose: every preview click regression-tests
the machinery fills depend on.

The rule is PER USER: your next click abandons your own unclaimed
preview runs (a run nobody is polling has nobody to show its answer
to) and starts a new one. Nobody else's runs are read: teammates queue
behind each other on the consumer, never refuse each other at the door,
and a forgotten run either lands or is pruned by age. Superseding and
cancelling reach a run that has NOT been claimed; a run a consumer
already owns runs to its own terminal write (in-flight spend is sunk
cost, and its result still lands).

Account-scoped like every lists service; the age prune is the cron's
(operations/prune_preview_runs.py), global."""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from openbower_kernel.ranks import first_key
from openbower_schema.agents import (
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_VALUE_MAX_LENGTH,
    AgentConfig,
)
from openbower_schema.fills import CellRunResult

from ..constants import NodeRunStatus, PreviewErrorCode
from ..models import NodeRun
from .node_runs import NodeRunFlow
from .runnable import ConfigUnrunnable, check_runnable
from .workflows import WorkflowService


class PreviewRefused(Exception):
    """A preview start the server refuses: `code` is the machine leg of
    the {error, detail} envelope, the message is copy rendered
    verbatim."""

    code: PreviewErrorCode = PreviewErrorCode.TEST_REFUSED


class PreviewRowInvalid(PreviewRefused):
    """A hand-fed row past the wire's preview bounds: REFUSED, never
    truncated, because a truncated test would diagnose a different row
    than the user typed. The bounds ship in x-constants, so a client
    can make this refusal unreachable; the copy names the bound that
    fired."""

    code = PreviewErrorCode.TEST_ROW_INVALID


class PreviewUnrunnable(PreviewRefused):
    """The drafted config cannot run at all (no model, an unknown
    tool): refused before a run exists, so a preview click never starts
    a run that crashes on its one row."""

    code = PreviewErrorCode.MODEL_UNRUNNABLE


class PreviewRunNotFound(Exception):
    """Missing OR foreign run (cross-tenant reads as not-found), or a
    run that is not a preview run at all."""


def preview_runs() -> QuerySet[NodeRun]:
    """Every preview run, any account: the agent runs that carry their
    own input, by the model's one spelling of that fact."""
    return NodeRun.objects.filter(**NodeRun.preview_lookup())


class PreviewRunService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def start(self, *, config: AgentConfig, row: dict) -> NodeRun:
        """One READY preview run of the account's preview node, carrying the
        drafted config and the row. The row's bounds and the config's
        runnability refuse BEFORE anything is written; the supersede
        runs unlocked (the worker's terminal CAS makes a double abandon
        a no-op, and two simultaneous clicks of yours leave two live
        runs until the next click abandons both, which the rule
        tolerates)."""
        self._check_row(row)
        self._check_config(config)
        self._supersede_own()
        now = timezone.now()
        with transaction.atomic():
            # The preview node: the account's one sheetless column_agent
            # node, so the run has a node to be a run of. The drafted
            # config rides the run, never the node.
            preview = WorkflowService(account_id=self.account_id).get_or_create_preview_node()
            return NodeRun.objects.create(
                account_id=self.account_id,
                fill_run_id=None,
                node_id=str(preview.id),
                kind=preview.kind,
                row_id=None,
                list_id="",
                rank=first_key(),
                status=NodeRunStatus.READY,
                input={"row": row, "config": config.model_dump()},
                started_by=self.user_id,
                last_state_change_at=now,
            )

    def get(self, run_id: str) -> NodeRun:
        run = preview_runs().filter(id=run_id, account_id=self.account_id).first()
        if run is None:
            raise PreviewRunNotFound(run_id)
        return run

    def cancel(self, run_id: str) -> NodeRun:
        """Abandon a preview run that has not been claimed (READY or
        QUEUED). A run a consumer already owns runs to its own terminal
        CAS: in-flight spend is sunk cost, and its result still lands
        (a cancel racing the landing must not strand a paid diagnosis).
        A run already terminal is a no-op, not an error."""
        run = self.get(run_id)
        NodeRunFlow.abandon_runs([str(run.id)])
        run.refresh_from_db()
        return run

    @staticmethod
    def result(run: NodeRun) -> CellRunResult | None:
        """The run's stored result, read back through the contract model
        it was written through; None until the run finished with one."""
        if run.status != NodeRunStatus.DONE or not run.result:
            return None
        return CellRunResult.model_validate(run.result)

    @staticmethod
    def _check_row(row: dict) -> None:
        """The preview bounds, refused HERE so the refusal rides the
        {error, detail} envelope every other refusal uses (a
        serializer-raised bound answers DRF's field shape, which the
        client cannot read). First, before the model gate: a row the
        wire refuses needs no probe."""
        if len(row) > TEST_ROW_MAX_KEYS:
            raise PreviewRowInvalid(f"a test row carries at most {TEST_ROW_MAX_KEYS} values")
        if any(len(key) > TEST_KEY_MAX_LENGTH for key in row):
            raise PreviewRowInvalid(f"row keys are bounded at {TEST_KEY_MAX_LENGTH} characters")
        if any(len(value) > TEST_VALUE_MAX_LENGTH for value in row.values()):
            raise PreviewRowInvalid(f"row values are bounded at {TEST_VALUE_MAX_LENGTH} characters")

    @staticmethod
    def _check_config(config: AgentConfig) -> None:
        """Start-time UX only; the worker's claim-time resolution is
        authoritative. The toggles resolve here too: a toggle naming no
        registered tool fails the row identically."""
        try:
            check_runnable(config)
        except ConfigUnrunnable as e:
            raise PreviewUnrunnable(str(e)) from e

    def _supersede_own(self) -> None:
        """Abandon this user's unclaimed preview runs. The read states
        the open states and the null fill on purpose: with them it
        rides the open-run key's partial index (a seek on the null
        row), without them it scans every preview run."""
        own = preview_runs().filter(
            account_id=self.account_id,
            started_by=self.user_id,
            status__in=(NodeRunStatus.READY, NodeRunStatus.QUEUED),
        )
        NodeRunFlow.abandon_runs([str(run_id) for run_id in own.values_list("id", flat=True)])

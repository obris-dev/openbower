"""The TEST kind's admission service: the bench's one-row diagnostic
admitted as a fill. No columns write, no consent echo, no row scan,
no ephemeral agent, no account cap; what it shares with the normal
kind is exactly the base (custody and the model gate) plus the
lifecycle everything downstream of admission already shares."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from openbower_kernel.fields import min_ulid_at
from openbower_schema.agents import (
    TEST_KEY_MAX_LENGTH,
    TEST_ROW_MAX_KEYS,
    TEST_VALUE_MAX_LENGTH,
    AgentConfig,
)

from ...constants import LIVE_FILL_STATUSES, FillKind, NodeRunStatus
from ...models import Fill, NodeRun
from .. import fill_progress
from ..fingerprint import config_fingerprint
from ..node_runs import PROCESSING_STALE_SECONDS
from ..workflows import WorkflowService
from .base import AdmissionBase
from .errors import TestFillActive, TestRowInvalid


class TestFillAdmission(AdmissionBase):
    def admit(self, *, config: AgentConfig, row: dict) -> Fill:
        """The bench's one-row diagnostic as a FILL (kind=test): same
        lifecycle, same worker, no sheet custody. No columns write, no
        consent echo, no ephemeral agent (the drafted config IS the
        custody; agent_id stays blank, and the run's node is the
        account's bench node). The row is INLINE, the one
        source: the bench's hand-fed values, bounds-refused by
        _check_row below, riding the fill itself; a test fill never
        points at a sheet (list_id stays blank BY CONSTRUCTION, which
        is what keeps every list-scoped fill read structurally clear
        of the test lane). Your own live test is superseded; a
        teammate's fresh one refuses. NO account cap: the bench must
        always answer, and supersede bounds the lane at one live test,
        so an account at the fill cap runs at most one over (the live
        test still counts into the cap normal admissions obey; spend
        is spend)."""
        self._check_row(row)
        self._check_model(config)
        # The supersede runs BEFORE the create transaction, unlocked.
        # It must: stop_fill takes NodeRun before Fill (the order the
        # cancel view and the worker's fail leg share), so holding the
        # Fill row lock here and then cancelling inverted that order
        # against every concurrent stopper (an ABBA deadlock). The
        # arbitration stop_fill's own CAS already provides is enough,
        # and a fill someone else stopped first is a no-op.
        self._supersede_or_refuse_tests()
        with transaction.atomic():
            fill = Fill.objects.create(
                account_id=self.account_id,
                user_id=self.user_id,
                kind=FillKind.TEST,
                list_id="",
                agent_id="",
                column_keys=[output.key for output in config.outputs],
                config_snapshot=config.model_dump(),
                config_fingerprint=config_fingerprint(config),
                confirmed_row_count=1,
                # The bench's one run is created right here: whole at birth.
                targeted_at=timezone.now(),
                # Stored as a LIST (position-indexed by tasks): one row
                # from this endpoint today, shaped for inline example
                # lists tomorrow.
                row_data=[row],
            )
            # The bench node: the account's one sheetless column_agent
            # node, so a test run has a node to be a run of. The drafted
            # config rides the fill's snapshot, never the node.
            bench = WorkflowService(account_id=self.account_id).get_or_create_bench_node()
            # row_id NULL: the row is inline (`row_data[position]`) and no
            # ListRow exists for it. NULL is distinct under the
            # (fill_run_id, row_id) key, so N inline rows never collide.
            NodeRun.objects.create(
                account_id=self.account_id,
                fill_run_id=str(fill.id),
                node_id=str(bench.id),
                kind=bench.kind,
                row_id=None,
                list_id=fill.list_id,  # "" by construction: a bench fill points at no sheet
                position=0,
                status=NodeRunStatus.READY,
                last_state_change_at=timezone.now(),
            )
        return fill

    @staticmethod
    def _check_row(row: dict) -> None:
        """The bench bounds, refused AT ADMISSION so the refusal rides
        the {error, detail} envelope every other refusal uses (a
        serializer-raised bound answered DRF's field shape, which the
        client cannot read). First, before the model gate: a row the
        wire refuses needs no probe."""
        if len(row) > TEST_ROW_MAX_KEYS:
            raise TestRowInvalid(f"a test row carries at most {TEST_ROW_MAX_KEYS} values")
        if any(len(key) > TEST_KEY_MAX_LENGTH for key in row):
            raise TestRowInvalid(f"row keys are bounded at {TEST_KEY_MAX_LENGTH} characters")
        if any(len(value) > TEST_VALUE_MAX_LENGTH for value in row.values()):
            raise TestRowInvalid(f"row values are bounded at {TEST_VALUE_MAX_LENGTH} characters")

    def _supersede_or_refuse_tests(self) -> None:
        """One live test per account, held ADVISORILY. Liveness is the
        tasks' latest state change (or, none moved yet, the ULID birth)
        within PROCESSING_STALE_SECONDS. A FRESH teammate's test refuses;
        everything else (your own, or anyone's stale orphan) is
        cancelled and superseded.

        The scan is UNLOCKED, deliberately: stop_fill's CAS makes a
        double cancel a no-op, and two simultaneous admissions that
        both pass this read leave two live tests until the next click
        supersedes both, which the uncapped lane already tolerates.
        A Fill row lock here would order Fill before NodeRun against
        every other stopper's NodeRun-before-Fill and deadlock."""
        now = timezone.now()
        fresh_cutoff = now - timedelta(seconds=PROCESSING_STALE_SECONDS)
        live = list(
            Fill.objects.filter(account_id=self.account_id, kind=FillKind.TEST, status__in=LIVE_FILL_STATUSES).order_by(
                "id"
            )
        )
        for fill in live:
            if fill.user_id == self.user_id:
                continue
            # Liveness DERIVED from the tasks' latest state change, not a
            # stored heartbeat. The stamp moves on TRANSITIONS (claim,
            # park, settle), not mid-run, so the window is
            # PROCESSING_STALE_SECONDS (the same bound the reclaim calls a
            # PROCESSING task dead at): a single-task test running its
            # bounded run_cell stays fresh the whole way, instead of
            # reading stale and being superseded mid-run. A run with no
            # task moved yet falls back to its ULID birth.
            heartbeat = NodeRun.objects.filter(fill_run_id=str(fill.id)).aggregate(latest=Max("last_state_change_at"))[
                "latest"
            ]
            fresh = heartbeat >= fresh_cutoff if heartbeat is not None else str(fill.id) >= min_ulid_at(fresh_cutoff)
            if fresh:
                raise TestFillActive()
        for fill in live:
            fill_progress.cancel(str(fill.id))

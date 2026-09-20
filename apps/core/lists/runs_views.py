"""`/v1/runs`: the run-scoped endpoints (the bench's start, a run read
by id, run cancel). A SECOND urlconf beside `/v1/lists` because these
routes are not list-scoped: a bench run has no sheet at all. Views
stay thin dispatch; the lane lives in services.bench_runs."""

from __future__ import annotations

from functools import cached_property

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_schema.agents import AgentConfig

from .constants import BenchErrorCode
from .models import NodeRun
from .serializers import BenchRunRequest, node_run_wire
from .services.bench_runs import BenchRefused, BenchRunNotFound, BenchRunService

# The run-scoped conflict partition (the lists views' idiom): a 409 is
# a conflict with live state the same request survives later; every
# other refusal is a 400.
_RUN_CONFLICT_CODES = frozenset({BenchErrorCode.TEST_ACTIVE})


class _ScopedView(ScopedView):
    @cached_property
    def bench(self) -> BenchRunService:
        return BenchRunService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    def _run_or_404(self, run_id: str) -> NodeRun:
        try:
            return self.bench.get(run_id)
        except BenchRunNotFound as e:
            raise NotFound("no run with that id") from e

    def _wire(self, run: NodeRun) -> dict:
        return node_run_wire(run, self.bench.result(run))


class BenchRunView(_ScopedView):
    """POST /v1/runs/bench {config, row} -> 202: the bench's one-row
    diagnostic as a run that owns its input; the autofill consumer runs
    it and the result lands on GET /v1/runs/{id}. 202 on purpose: this
    is the polled-run idiom (accepted, watch the run), the same answer
    discover's lookalike run gives."""

    def post(self, request: Request) -> Response:
        serializer = BenchRunRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            run = self.bench.start(config=AgentConfig(**data["config"]), row=data["row"])
        except BenchRefused as e:
            status = 409 if e.code in _RUN_CONFLICT_CODES else 400
            return Response({"error": e.code, "detail": str(e)}, status=status)
        return Response(self._wire(run), status=202)


class RunDetailView(_ScopedView):
    """GET /v1/runs/{id}: one bench run, account-scoped (a foreign run
    reads as missing), with its stored result once it finished."""

    def get(self, request: Request, id: str) -> Response:
        return Response(self._wire(self._run_or_404(id)))


class RunCancelView(_ScopedView):
    """POST /v1/runs/{id}/cancel: abandon a bench run that has not been
    claimed. The echo is exactly what GET serves: a cancel that raced
    the landing answers the stored result, never null."""

    def post(self, request: Request, id: str) -> Response:
        self._run_or_404(id)
        return Response(self._wire(self.bench.cancel(id)))

"""`/v1/runs`: the run-scoped endpoints (the preview's start, a run read
by id, run cancel). A SECOND urlconf beside `/v1/lists` because these
routes are not list-scoped: a preview run has no sheet at all. Views
stay thin dispatch; the lane lives in services.preview_runs."""

from __future__ import annotations

from functools import cached_property

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_schema.agents import AgentConfig

from .models import NodeRun
from .serializers import PreviewRunRequest, node_run_wire
from .services.preview_runs import PreviewRefused, PreviewRunNotFound, PreviewRunService


class _ScopedView(ScopedView):
    @cached_property
    def preview(self) -> PreviewRunService:
        return PreviewRunService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    def _run_or_404(self, run_id: str) -> NodeRun:
        try:
            return self.preview.get(run_id)
        except PreviewRunNotFound as e:
            raise NotFound("no run with that id") from e

    def _wire(self, run: NodeRun) -> dict:
        return node_run_wire(run, self.preview.result(run))


class PreviewRunView(_ScopedView):
    """POST /v1/runs/preview {config, row} -> 202: the preview's one-row
    diagnostic as a run that owns its input; the autofill consumer runs
    it and the result lands on GET /v1/runs/{id}. 202 on purpose: this
    is the polled-run idiom (accepted, watch the run), the same answer
    discover's lookalike run gives."""

    def post(self, request: Request) -> Response:
        serializer = PreviewRunRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            run = self.preview.start(config=AgentConfig(**data["config"]), row=data["row"])
        except PreviewRefused as e:
            # Every preview refusal is a 400: nothing at the door is a
            # conflict with live state (a teammate's run never refuses).
            return Response({"error": e.code, "detail": str(e)}, status=400)
        return Response(self._wire(run), status=202)


class RunDetailView(_ScopedView):
    """GET /v1/runs/{id}: one preview run, account-scoped (a foreign run
    reads as missing), with its stored result once it finished."""

    def get(self, request: Request, id: str) -> Response:
        return Response(self._wire(self._run_or_404(id)))


class RunCancelView(_ScopedView):
    """POST /v1/runs/{id}/cancel: abandon a preview run that has not been
    claimed. The echo is exactly what GET serves: the run as it is
    after the cancel (abandoned, or untouched if a consumer already
    owned it or it had finished, with its result then)."""

    def post(self, request: Request, id: str) -> Response:
        try:
            run = self.preview.cancel(id)
        except PreviewRunNotFound as e:
            raise NotFound("no run with that id") from e
        return Response(self._wire(run))

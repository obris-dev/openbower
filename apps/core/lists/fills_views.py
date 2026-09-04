"""`/v1/fills`: the run-scoped endpoints (a run read by id, the
bench's test-fill create, run cancel). A SECOND urlconf beside
`/v1/lists` because these routes are not list-scoped: an inline test
fill has no sheet at all. Views stay thin dispatch; admission and
custody live in the services."""

from __future__ import annotations

from functools import cached_property

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_schema.agents import AgentConfig

from .constants import FillErrorCode
from .models import Fill
from .serializers import TestFillRequest, fill_run_detail_wire
from .services.fill_admission import FillRefused, TestFillAdmission
from .services.fills import FillNotFound, FillService

# The run-scoped conflict partition (the lists views' idiom): a 409 is
# a conflict with live state the same request survives later; every
# other refusal is a 400. A SINGLETON, and that is the receipt for the
# no-cap ruling: the test admission runs no account cap, so fills_full
# can never fire here.
_RUN_CONFLICT_CODES = frozenset({FillErrorCode.TEST_ACTIVE})


class _ScopedView(ScopedView):
    @cached_property
    def fills(self) -> FillService:
        return FillService(account_id=self.request.user.account_id)

    @cached_property
    def test_admission(self) -> TestFillAdmission:
        return TestFillAdmission(account_id=self.request.user.account_id, user_id=self.request.user.id)

    def _fill_or_404(self, fill_id: str) -> Fill:
        try:
            return self.fills.get(fill_id)
        except FillNotFound as e:
            raise NotFound("no run with that id") from e


class TestFillView(_ScopedView):
    """POST /v1/fills/test {config, row} -> 202: the bench's one-row
    diagnostic admitted as a test-kind fill; the worker runs it and
    the result lands on GET /v1/fills/{id}. 202, not the admissions'
    201, on purpose: this is the polled-run idiom (accepted, watch the
    run), the same answer discover's lookalike run gives."""

    def post(self, request: Request) -> Response:
        serializer = TestFillRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            fill = self.test_admission.admit(config=AgentConfig(**data["config"]), row=data["row"])
        except FillRefused as e:
            status = 409 if e.code in _RUN_CONFLICT_CODES else 400
            return Response({"error": e.code, "detail": str(e)}, status=status)
        return Response(fill_run_detail_wire(fill, None), status=202)


class FillRunDetailView(_ScopedView):
    """GET /v1/fills/{id}: one run, account-scoped (a foreign run
    reads as missing), with a COMPLETE test run's stored result."""

    def get(self, request: Request, id: str) -> Response:
        fill = self._fill_or_404(id)
        return Response(fill_run_detail_wire(fill, self.fills.test_result(fill)))


class FillRunCancelView(_ScopedView):
    """POST /v1/fills/{id}/cancel: stop a run wherever it is scoped
    (an inline test fill has no list for the list-scoped cancel)."""

    def post(self, request: Request, id: str) -> Response:
        fill = self._fill_or_404(id)
        cancelled = self.fills.cancel(str(fill.id))
        # The result rides the echo exactly as GET serves it: a cancel
        # of an already-COMPLETE run must not answer result: null where
        # the detail read answers the stored record.
        return Response(fill_run_detail_wire(cancelled, self.fills.test_result(cancelled)))

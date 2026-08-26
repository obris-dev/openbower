"""`/v1/agents`: the roster's CRUD, the runnable-models catalog, and
the test bench (session-authed like lists). Views are thin dispatch:
persistence lives in the services, execution in the runtime, and wire
shapes construct through the contract models at these boundaries."""

from __future__ import annotations

import logging
import threading
from functools import cached_property

from django.conf import settings
from pydantic_ai.models import Model
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from lists.constants import FillErrorCode
from openbower_schema.agents import AgentCatalog, AgentConfig, AgentsList, CatalogModel, TestSearch
from openbower_schema.fills import CellRunResult

from .constants import TEST_RUN_MAX_CONCURRENT
from .models import Agent
from .providers import ModelUnavailable, catalog_entries, model_for
from .search import SearchMisconfigured, contacts_available, search_available
from .serializers import (
    AgentCreateRequest,
    AgentPatchRequest,
    AgentTestRequest,
    agent_wire,
    list_item_wire,
    test_run_wire,
)
from .services import (
    AgentNotFound,
    AgentService,
    AgentsFull,
    PaidLanesFull,
    TestRunActive,
    TestRunNotFound,
    TestRunService,
    complete_run,
    config_fingerprint,
    fail_run,
    run_is_pending,
)

logger = logging.getLogger(__name__)

# The 409's machine-readable code (the discover/auth envelope idiom).
TEST_RUN_ACTIVE_CODE = "test_run_active"


# Named so the parity pin can hold prefix + bounded follow-up inside
# fail_run's error clamp (a composed message must never truncate).
CRASH_MESSAGE_PREFIX = "the run crashed unexpectedly; run the test again, and if it keeps happening, "
# Composed at MODULE LOAD, deliberately: the crash handler must touch
# no settings inside its except block (a future profile gap would
# turn a crashed run into a stuck-pending one there; here it fails
# the boot instead).
CRASH_MESSAGE = CRASH_MESSAGE_PREFIX + settings.SUPPORT_FOLLOWUP


class _ScopedView(ScopedView):
    @cached_property
    def agents(self) -> AgentService:
        return AgentService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def test_runs(self) -> TestRunService:
        return TestRunService(account_id=self.request.user.account_id)

    def _agent_or_404(self, agent_id: str) -> Agent:
        try:
            return self.agents.get(agent_id)
        except AgentNotFound as e:
            raise NotFound("no agent with that id") from e


class AgentsView(_ScopedView):
    def get(self, request: Request) -> Response:
        items = [list_item_wire(a) for a in self.agents.list()]
        return Response(AgentsList(items=items).model_dump())

    def post(self, request: Request) -> Response:
        serializer = AgentCreateRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            agent = self.agents.create(label=data["label"], config=AgentConfig(**data["config"]))
        except AgentsFull as e:
            raise ValidationError(str(e)) from e
        return Response(agent_wire(agent), status=201)


class AgentCatalogView(_ScopedView):
    """GET /v1/agents/catalog: what THIS deploy can run. Models come
    from the configured doors; the availability flags gate the tools."""

    def get(self, request: Request) -> Response:
        entries, truncated = catalog_entries()
        models = [CatalogModel(provider=provider, source=source, model=model) for provider, source, model in entries]
        wire = AgentCatalog(
            models=models,
            support_followup=settings.SUPPORT_FOLLOWUP,
            truncated=truncated,
            search_available=search_available(),
            contacts_available=contacts_available(),
        )
        return Response(wire.model_dump())


# The per-process backstop for paid work (the account-wide invariant
# lives in the start guard): Bounded so a stray release raises
# instead of silently growing the cap.
_TEST_SLOTS = threading.BoundedSemaphore(TEST_RUN_MAX_CONCURRENT)


def _spawn_test(run_id: str, config: AgentConfig, row: dict, model: Model) -> None:
    threading.Thread(target=_execute_test, args=(run_id, config, row, model), daemon=True).start()


def _execute_test(
    run_id: str, config: AgentConfig, row: dict, model: Model | None = None, *, close_connection: bool = True
) -> None:
    """The background half of a test run: the cell walk, then the row
    flips terminal. Runs on a daemon thread with its own DB connection
    (closed on exit so threads never leak connections); ANY failure
    lands as status=failed WITH its why, never a stuck pending."""
    from django.db import connection

    from .runtime import run_cell

    try:
        # REFUSE at capacity, never queue: queue time is invisible to
        # the published poll budget, and a queued run could present as
        # interrupted having never started.
        if not _TEST_SLOTS.acquire(blocking=False):
            fail_run(run_id, "this deployment is at its test capacity; run the test again in a moment")
            return
        try:
            # The tombstone gate covers the WORK, not just the final
            # write: a run superseded while this thread was scheduled
            # must not buy its completions and searches.
            if not run_is_pending(run_id):
                logger.info("test run %s superseded before start; skipping", run_id)
                return
            run = run_cell(config, row, model=model)
            # Wire-shaping happens HERE, at the boundary, THROUGH the
            # contract models: drift fails at write, in the code that
            # caused it.
            result = CellRunResult(
                cells=run.cells,
                evidence=run.evidence,
                searches=[TestSearch(query=o.query, hits=len(o.hits), failed=o.failed) for o in run.searches],
                blank_cause=run.blank_cause,
                declined_cause=run.declined_cause,
                assessments=run.assessments,
            )
            complete_run(run_id, result.model_dump())
        finally:
            _TEST_SLOTS.release()
    except (ModelUnavailable, SearchMisconfigured) as e:
        # Config-tier refusals carry their own user-facing why.
        logger.exception("test run %s failed", run_id)
        fail_run(run_id, str(e))
    except Exception:
        # Anything else is internal detail; the wire gets a next step,
        # the logs keep the traceback.
        logger.exception("test run %s failed", run_id)
        # The follow-up is PROFILE-owned: local can honestly say "check
        # the logs" (they are right there); cloud names its support
        # channel. A hedge covering both served neither.
        fail_run(run_id, CRASH_MESSAGE)
    finally:
        if close_connection:
            connection.close()


class AgentTestView(_ScopedView):
    """POST /v1/agents/test {config, row} -> 202 {id, status}: start a
    run of the DRAFTED config (saved or not; both custodies and the
    builder share this) against one hand-fed row, then POLL the run
    (the bench must not hold a connection for the seconds a run
    takes). An unrunnable ADDRESS refuses HERE, loudly, before a run
    row exists: a config error must never masquerade as a started run."""

    def post(self, request: Request) -> Response:
        serializer = AgentTestRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = AgentConfig(**serializer.validated_data["config"])
        try:
            model = model_for(config.provider, config.source, config.model)
        except ModelUnavailable as e:
            raise ValidationError(str(e)) from e
        # Account-level admission BEFORE a run row exists: the bench
        # is a metered lane like a fill, so the fill lane's cap gates
        # it too (same envelope shape, classified by code).
        try:
            # The bench is a metered lane like a fill, so the fill
            # lane's account cap gates it too. The fill lane's own
            # code, imported not re-spelled: one taxonomy leg for "the
            # account's paid lanes are full", whichever lane answers.
            self.test_runs.assert_lane_available()
            run = self.test_runs.start(
                user_id=self.request.user.id,
                config_fingerprint=config_fingerprint(config),
                row_id=serializer.validated_data["row_id"],
            )
        except PaidLanesFull as e:
            return Response({"error": FillErrorCode.FILLS_FULL, "detail": str(e)}, status=409)
        except TestRunActive as e:
            # The sibling envelope shape ({error: <code>, detail}): a
            # client classifies by CODE, and renders the detail
            # verbatim. No run id: adopting a teammate's run would
            # show their cells under your config's types and diagnoses.
            return Response({"error": TEST_RUN_ACTIVE_CODE, "detail": str(e)}, status=409)
        _spawn_test(str(run.id), config, serializer.validated_data["row"], model)
        return Response(test_run_wire(run), status=202)


class AgentTestRunView(_ScopedView):
    """GET /v1/agents/test/{id}: the poll leg."""

    def get(self, request: Request, id: str) -> Response:
        try:
            run = self.test_runs.poll(id, user_id=self.request.user.id)
        except TestRunNotFound as e:
            raise NotFound("no test run with that id") from e
        return Response(test_run_wire(run))


class AgentDetailView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        return Response(agent_wire(self._agent_or_404(id)))

    def patch(self, request: Request, id: str) -> Response:
        agent = self._agent_or_404(id)
        serializer = AgentPatchRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = AgentConfig(**data["config"]) if data.get("config") else None
        return Response(agent_wire(self.agents.update(agent, label=data.get("label"), config=config)))

    def delete(self, request: Request, id: str) -> Response:
        self.agents.delete(self._agent_or_404(id))
        return Response(status=204)

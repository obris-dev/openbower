"""`/v1/agents`: the roster's CRUD and the runnable-models catalog
(session-authed like lists). Views are thin dispatch:
persistence lives in the services, execution in the runtime, and wire
shapes construct through the contract models at these boundaries."""

from __future__ import annotations

import logging
from functools import cached_property

from django.conf import settings
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_schema.agents import AgentCatalog, AgentConfig, AgentsList, CatalogModel

from .models import Agent
from .providers import catalog_entries
from .serializers import (
    AgentCreateRequest,
    AgentPatchRequest,
    agent_wire,
    list_item_wire,
)
from .services import (
    AgentNotFound,
    AgentService,
    AgentsFull,
)
from .tools import registry as tool_registry
from .tools.search import web_search

logger = logging.getLogger(__name__)


class _ScopedView(ScopedView):
    @cached_property
    def agents(self) -> AgentService:
        return AgentService(account_id=self.request.user.account_id)

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
            agent = self.agents.create(
                owner_id=self.request.user.id, label=data["label"], config=AgentConfig(**data["config"])
            )
        except AgentsFull as e:
            raise ValidationError(str(e)) from e
        return Response(agent_wire(agent), status=201)


class AgentCatalogView(_ScopedView):
    """GET /v1/agents/catalog: what THIS deploy can run. Models come
    from the configured providers; `tools` gates the toggles."""

    def get(self, request: Request) -> Response:
        entries, truncated = catalog_entries()
        models = [CatalogModel(provider=provider, source=source, model=model) for provider, source, model in entries]
        wire = AgentCatalog(
            models=models,
            support_followup=settings.SUPPORT_FOLLOWUP,
            truncated=truncated,
            tools={tool.name: tool.availability().value for tool in tool_registry.all_tools()},
            # The vendor serving web search only when it is READY to
            # serve (null otherwise, an unwired or credential-less
            # deploy alike): the web's copy never names a vendor whose
            # searches cannot run.
            search_provider=web_search.open_vendor(),
        )
        return Response(wire.model_dump())


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

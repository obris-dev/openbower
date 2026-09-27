"""The AI column's own writes: the column, its agent and its node. No
fill: filling is the column's gesture (FillAdmissionService.fill_column),
from the drawer right after this or from the column's tracker later, so
an AI column can exist with rows never tried, which its tracker offers
to fill. Beside WebhookColumnService, the other workflow column's.
Account-scoped like every lists service."""

from __future__ import annotations

from django.db import transaction

from agents.models import Agent
from agents.services import AgentService
from openbower_schema.agents import LABEL_MAX_LENGTH as AGENT_LABEL_MAX_LENGTH
from openbower_schema.agents import AgentConfig

from ..models import List
from .columns import locked_list
from .fill_admission import ProviderRetiredRefusal
from .fill_admission.base import check_model
from .fill_admission.columns import append_columns, check_column_cap, resolve_columns
from .workflows import WorkflowService


class AiColumnService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.agents = AgentService(account_id=account_id)
        self.workflows = WorkflowService(account_id=account_id)

    def add(self, target_list_id: str, *, config: AgentConfig | None = None, agent_id: str = "") -> List:
        """The column set one agent's outputs make, bound to the node
        that fills them, in one transaction under the list lock (the
        same lock every columns writer takes). Exactly one of `config`
        (the quick tab: an ephemeral agent is created) or `agent_id` (a
        roster agent) is given; the serializer enforces it.

        The config is checked runnable BEFORE the transaction (the probe
        can be a network call, and nothing that slow belongs inside
        one), so a broken config never becomes a column. The column
        refusals (a key the sheet holds, the column cap) are judged
        under the lock before the agent or node is written, so a refused
        create writes nothing. The columns are born never run
        (`current_fill_id` ""); the fill that follows points them at
        itself."""
        agent, config = self._resolve_agent(config=config, agent_id=agent_id)
        check_model(config)
        with transaction.atomic():
            target_list = locked_list(self.account_id, target_list_id)
            column_keys = resolve_columns(target_list, config=config)
            check_column_cap(target_list, column_keys=column_keys)
            if agent is None:
                # The ephemeral row needs SOME label for custody
                # surfaces; the first output's is the least arbitrary.
                agent = self.agents.create_ephemeral(
                    owner_id=self.user_id,
                    label=config.outputs[0].label[:AGENT_LABEL_MAX_LENGTH],
                    config=config,
                )
            # The node binds this agent to this sheet: get-or-create, so a
            # second column set from the same roster agent reuses it (one
            # node per agent per sheet is the run's own grain).
            node = self.workflows.get_or_create_column_agent_node(target_list, agent_id=str(agent.id))
            append_columns(target_list, column_keys=column_keys, config=config, node_id=str(node.id))
        return target_list

    def _resolve_agent(self, *, config: AgentConfig | None, agent_id: str) -> tuple[Agent | None, AgentConfig]:
        if config is not None:
            return None, config
        # Roster-only on purpose: an ephemeral row belongs to exactly one
        # node, so a second column add can never reach it.
        agent = self.agents.get(agent_id)
        if agent.provider_retired:
            raise ProviderRetiredRefusal()
        return agent, agent.config()

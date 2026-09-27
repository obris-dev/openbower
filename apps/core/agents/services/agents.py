"""Agent roster services: the Agent model's ORM custody (account
scoped; cross-tenant access fails as not-found)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from typing import TYPE_CHECKING

from openbower_schema.agents import AgentConfig, AgentOutput

from ..constants import MAX_AGENTS, AgentErrorCode
from ..models import Agent

if TYPE_CHECKING:
    from lists.services.workflows import AgentColumnUse, WorkflowService


class AgentsFull(Exception):
    """Another CONFIGURED agent would exceed MAX_AGENTS (ephemeral rows
    never count; they are bounded by the columns that own them)."""


class AgentNotFound(Exception):
    """Missing OR foreign agent (cross-tenant reads as not-found)."""


class AgentOutputsInUse(Exception):
    """The save changes the output set (a key or a type added, removed,
    or changed) of an agent whose columns are on a sheet: those columns
    are the outputs' shape, fixed while they exist."""

    code = AgentErrorCode.OUTPUTS_IN_USE

    def __init__(self, uses: list[AgentColumnUse]) -> None:
        self.uses = uses
        # One sheet is named; more are counted. The user set the agent
        # on those sheets and each column carries its label, so a list
        # of names says nothing new and grows without bound in a toast.
        where = uses[0].label if len(uses) == 1 else f"{len(uses)} sheets"
        super().__init__(
            f"This agent fills columns on {where}. Its outputs can't change while those columns exist: "
            "create a new agent with the outputs you need, or delete those columns first."
        )


def _output_shape(outputs: Sequence[AgentOutput]) -> set[tuple[str, str]]:
    """The output set as its columns see it: each output's (key, type).
    Order and labels are not part of it (a column is ordered and labeled
    on its sheet)."""
    return {(output.key, output.type) for output in outputs}


class AgentService:
    """Account-scoped: every method reads or writes within one account.
    The owner-stamping ops (create, create_ephemeral) take the owner as an
    explicit `owner_id` argument rather than the service carrying a user_id
    the account-scoped reads would ignore."""

    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    @cached_property
    def workflows(self) -> WorkflowService:
        # lists imports this module at load, so the edge back is deferred
        # to first use; a module-level import here is a startup cycle.
        from lists.services.workflows import WorkflowService

        return WorkflowService(account_id=self.account_id)

    def _roster_count(self) -> int:
        return Agent.objects.filter(account_id=self.account_id, ephemeral=False).count()

    def create(self, *, owner_id: str, label: str, config: AgentConfig) -> Agent:
        """A config travels as the CONTRACT MODEL, here as everywhere
        (never exploded into loose primitives)."""
        if self._roster_count() >= MAX_AGENTS:
            raise AgentsFull(f"an account holds at most {MAX_AGENTS} agents")
        return Agent.objects.create(
            account_id=self.account_id,
            user_id=owner_id,
            label=label,
            provider=config.provider,
            source=config.source,
            model=config.model,
            prompt=config.prompt,
            tools=config.tools.model_dump(),
            outputs=[output.model_dump() for output in config.outputs],
        )

    def delete_ephemeral(self, agent_ids: list[str]) -> int:
        """Best-effort cleanup of the ephemeral rows a deleted surface
        owned. Account-scoped and ephemeral-only, so a roster agent a
        column happened to point at is never touched. Best effort by
        design: the columns are already gone, so a row that resists
        deletion is invisible litter, never a broken sheet."""
        if not agent_ids:
            return 0
        deleted, _ = Agent.objects.filter(id__in=agent_ids, account_id=self.account_id, ephemeral=True).delete()
        return deleted

    def create_ephemeral(self, *, owner_id: str, label: str, config: AgentConfig) -> Agent:
        """The column custody's constructor: hidden from the roster,
        EXCLUDED from MAX_AGENTS (ephemeral rows are bounded by the
        columns that own them, one each), deleted with its column. An
        ephemeral agent leaves this custody only by an explicit
        promotion, never by appearing in list()."""
        return Agent.objects.create(
            account_id=self.account_id,
            user_id=owner_id,
            label=label,
            ephemeral=True,
            provider=config.provider,
            source=config.source,
            model=config.model,
            prompt=config.prompt,
            tools=config.tools.model_dump(),
            outputs=[output.model_dump() for output in config.outputs],
        )

    def get_for_fill(self, agent_id: str) -> Agent:
        """The fill machinery's accessor, ephemeral-INCLUSIVE: a
        column's agent may be either custody. get() stays roster-only
        so the builder and roster paths cannot reach a column's
        private row."""
        try:
            return Agent.objects.get(id=agent_id, account_id=self.account_id)
        except Agent.DoesNotExist as e:
            raise AgentNotFound(agent_id) from e

    def list(self) -> list[Agent]:
        """The roster: configured agents only, newest first."""
        return list(Agent.objects.filter(account_id=self.account_id, ephemeral=False).order_by("-id"))

    def get(self, agent_id: str) -> Agent:
        """CONFIGURED rows only: ephemeral rows are phase 5's column
        custody and not addressable through the roster's paths (the
        column machinery gets its own accessor when it lands)."""
        try:
            return Agent.objects.get(id=agent_id, account_id=self.account_id, ephemeral=False)
        except Agent.DoesNotExist as e:
            raise AgentNotFound(agent_id) from e

    def update(self, agent: Agent, *, label: str | None = None, config: AgentConfig | None = None) -> Agent:
        """Label and/or the whole config (per-field config patching
        earns nothing over resending it). Refuses a config that changes
        the output set while the agent's columns are on a sheet
        (AgentOutputsInUse); the prompt, model, tools, and output labels
        stay editable.

        A courtesy guard, not a ledger: a column create racing this save
        can still bind the old outputs, whose damage is columns that stop
        filling, so no lock is taken."""
        if config is not None:
            self._refuse_output_change(agent, config)
        updates: list[str] = []
        if label is not None:
            agent.label = label
            updates.append("label")
        if config is not None:
            agent.provider = config.provider
            agent.source = config.source
            agent.model = config.model
            agent.prompt = config.prompt
            agent.tools = config.tools.model_dump()
            agent.outputs = [output.model_dump() for output in config.outputs]
            updates += ["provider", "source", "model", "prompt", "tools", "outputs"]
        if updates:
            agent.save(update_fields=[*updates, "updated_at"])
        return agent

    def _refuse_output_change(self, agent: Agent, config: AgentConfig) -> None:
        stored = agent.config()
        if _output_shape(stored.outputs) == _output_shape(config.outputs):
            return
        uses = self.workflows.agent_column_uses(str(agent.id))
        if uses:
            raise AgentOutputsInUse(uses)

    def promote(self, agent: Agent, *, label: str) -> Agent:
        """Lift an ephemeral row onto the roster: flip the flag and name
        it, copying nothing. A ROSTER ADMISSION, so the cap check runs
        here exactly as at create (skipping it would make promotion a
        cap bypass)."""
        if agent.ephemeral and self._roster_count() >= MAX_AGENTS:
            raise AgentsFull(f"an account holds at most {MAX_AGENTS} agents")
        agent.ephemeral = False
        agent.label = label
        agent.save(update_fields=["ephemeral", "label", "updated_at"])
        return agent

    def delete(self, agent: Agent) -> None:
        # Fetched through the account-scoped get(); no second guard.
        agent.delete()

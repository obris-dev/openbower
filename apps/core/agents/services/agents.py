"""Agent roster services: the Agent model's ORM custody (account
scoped; cross-tenant access fails as not-found). Test-run persistence
lives in runs.py."""

from __future__ import annotations

from openbower_schema.agents import AgentConfig

from ..constants import MAX_AGENTS
from ..models import Agent


class AgentsFull(Exception):
    """Another CONFIGURED agent would exceed MAX_AGENTS (ephemeral rows
    never count; they are bounded by the columns that own them)."""


class AgentNotFound(Exception):
    """Missing OR foreign agent (cross-tenant reads as not-found)."""


class AgentService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id

    def _roster_count(self) -> int:
        return Agent.objects.filter(account_id=self.account_id, ephemeral=False).count()

    def create(self, *, label: str, config: AgentConfig) -> Agent:
        """A config travels as the CONTRACT MODEL, here as everywhere
        (never exploded into loose primitives)."""
        if self._roster_count() >= MAX_AGENTS:
            raise AgentsFull(f"an account holds at most {MAX_AGENTS} agents")
        return Agent.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
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

    def create_ephemeral(self, *, label: str, config: AgentConfig) -> Agent:
        """The column custody's constructor: hidden from the roster,
        EXCLUDED from MAX_AGENTS (ephemeral rows are bounded by the
        columns that own them, one each), deleted with its column. An
        ephemeral agent leaves this custody only by an explicit
        promotion, never by appearing in list()."""
        return Agent.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
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
        earns nothing over resending it)."""
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

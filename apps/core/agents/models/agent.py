import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

from openbower_kernel.models import UserScopedModel
from openbower_schema.agents import AgentConfig, AgentTools

from ..coercion import coerce_config
from ..constants import (
    LABEL_MAX_LENGTH,
    MODEL_MAX_LENGTH,
    PROVIDER_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
    AgentProvider,
)

logger = logging.getLogger(__name__)


class Agent(UserScopedModel):
    """A named, reusable research configuration: one prompt that runs
    per row, a model choice, tool toggles, and a declared output schema
    (at least one output; every answer is strict JSON with those keys).

    The RUNTIME never touches this row: it runs a config dict the row
    composes (config()). An Agent row is one CUSTODY for a config; the
    other is an EPHEMERAL row created by a column's quick-prompt path
    (hidden from the roster, excluded from MAX_AGENTS, owned by exactly
    one column and deleted with it)."""

    label = models.CharField(_("label"), max_length=LABEL_MAX_LENGTH)
    # Bare CharField, validated at the serializer (the lists pattern):
    # choices= would turn every enum addition into a migration for
    # zero DB-side enforcement.
    provider = models.CharField(_("provider"), max_length=PROVIDER_MAX_LENGTH)
    # WHICH server of that spec (the env-named source); provider is the
    # spec, source the server, model the name: the full address.
    source = models.CharField(_("source"), max_length=SOURCE_MAX_LENGTH)
    model = models.CharField(_("model"), max_length=MODEL_MAX_LENGTH)
    prompt = models.TextField(_("prompt"))
    tools = models.JSONField(_("tools"), default=dict, help_text=_("{web_search, find_contacts} booleans"))
    outputs = models.JSONField(
        _("outputs"), default=list, help_text=_("[{key, label, type, description}], at least one")
    )
    ephemeral = models.BooleanField(_("ephemeral"), default=False)

    class Meta:
        verbose_name = _("agent")
        verbose_name_plural = _("agents")
        indexes = [models.Index(fields=["account_id", "id"], name="agent_account_idx")]
        constraints = [
            # Storage-side backing for the contract's min-1 outputs:
            # agent_wire VALIDATES on read, so one outputs=[] row (a
            # future producer's bug) would 500 the whole roster GET.
            models.CheckConstraint(condition=models.Q(outputs__0__isnull=False), name="agent_outputs_min1"),
        ]

    @property
    def provider_retired(self) -> bool:
        """Whether the stored provider names a spec no current release
        knows. config() COERCES it (render custody: the row must stay
        openable so the user can re-pick), which is safe only because
        nothing acts on config() today; any future path that RUNS a
        stored config must check this first and refuse (acting on the
        substituted spec would be a guess)."""
        return self.provider not in {p.value for p in AgentProvider}

    def tools_wire(self) -> dict[str, bool]:
        """Sanitized toggles for EVERY reader (config(), the list wire
        leg): junk storage (a non-dict, non-bool values) renders as
        defaults, never a 500 on the unpaged roster GET."""
        stored = self.tools if isinstance(self.tools, dict) else {}
        return {field: bool(stored.get(field)) for field in AgentTools.model_fields}

    def config(self) -> AgentConfig:
        """The runtime/interchange config this row is custody for,
        TYPED at construction (the contract model is the one shape a
        config ever travels as), and read TOLERANTLY: see
        agents.coercion, which the fill's frozen snapshot shares.

        The stored fields go in RAW. `provider_retired` stays as this
        custody's own public read, which the admission lane refuses on;
        the substitution itself belongs to the coercion, which performs
        the same test and is the one place that trails it."""
        stored = {
            "prompt": self.prompt,
            # RAW: the coercion performs this same test, so substituting
            # first left its warning comparing a value to itself and the
            # one case it exists to trail went silent. provider_retired
            # stays as the public read the admission lane refuses on.
            "provider": self.provider,
            "source": self.source,
            "model": self.model,
            "tools": self.tools,
            "outputs": self.outputs,
        }
        return coerce_config(stored, origin=f"agent {self.id}")

    def __str__(self) -> str:
        return self.label

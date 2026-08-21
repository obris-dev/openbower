import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

from lists.constants import ColumnType
from openbower_kernel.models import UserScopedModel
from openbower_schema.agents import AgentConfig, AgentTools

from ..constants import (
    LABEL_MAX_LENGTH,
    MAX_AGENT_OUTPUTS,
    MODEL_MAX_LENGTH,
    OUTPUT_DESCRIPTION_MAX_LENGTH,
    OUTPUT_KEY_MAX_LENGTH,
    OUTPUT_LABEL_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
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
        config ever travels as). Sizes CLAMP here: the contract's max
        bounds validate on READ, so a stored row over any of them (a
        future producer's bug, or a bound tightened after release)
        must render clamped, never 500 the GET that touches it."""
        # Enums coerce like lengths clamp: a stored type or provider a
        # later version retired must render (type falls to "text",
        # rendering-only anyway; a retired provider renders under the
        # first spec and normally refuses at run time because no
        # same-named source exists there; a deploy that DOES name one
        # identically under the substitute spec would run it there,
        # the render-over-refuse trade this read path makes).
        column_types = {t.value for t in ColumnType}
        stored_rows = self.outputs if isinstance(self.outputs, list) else []
        rows = [o for o in stored_rows if isinstance(o, dict)][:MAX_AGENT_OUTPUTS]
        outputs = [
            {
                "key": str(o.get("key", ""))[:OUTPUT_KEY_MAX_LENGTH],
                "label": str(o.get("label", ""))[:OUTPUT_LABEL_MAX_LENGTH],
                "type": o.get("type") if o.get("type") in column_types else "text",
                "description": str(o.get("description", ""))[:OUTPUT_DESCRIPTION_MAX_LENGTH],
            }
            for o in rows
        ]
        if not outputs:
            # The contract's min-1 must hold to RENDER at all; an
            # all-junk row set falls to one placeholder that NAMES its
            # brokenness (a run would write this column, and a cell
            # under "unreadable output" is a diagnosis, not data; the
            # log below is the trail to whatever wrote it).
            outputs = [{"key": "unreadable_output", "label": "Unreadable output", "type": "text", "description": ""}]
        provider = AgentProvider.OPENAI_COMPATIBLE.value if self.provider_retired else self.provider
        if outputs != stored_rows or provider != self.provider or len(self.prompt) > PROMPT_MAX_LENGTH:
            # Loud, not silent: a clamped or coerced read means some
            # producer wrote what the contract refuses; renderable
            # today, but the log is the trail to that producer.
            logger.warning("agent %s: stored config clamped/coerced on read", self.id)
        return AgentConfig(
            prompt=self.prompt[:PROMPT_MAX_LENGTH],
            provider=provider,
            source=self.source,
            model=self.model,
            tools=self.tools_wire(),
            outputs=outputs,
        )

    def __str__(self) -> str:
        return self.label

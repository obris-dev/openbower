"""Wire contract for the agents domain."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

from .lists import COLUMN_KEY_MAX_LENGTH, COLUMN_LABEL_MAX_LENGTH, ColumnType

AgentProvider = Literal["openai_compatible", "anthropic_compatible"]
# The SPEC coercion falls back to when a stored draft names a provider
# no current release knows: a door, never a source or a server (the
# user still picks the source). openai_compatible because it is the
# spec Ollama, vLLM, gateways, and the vendor all speak.
DEFAULT_AGENT_PROVIDER: AgentProvider = "openai_compatible"
# The specs as DATA (the SEARCH_PROVIDER_CHOICES idiom), for choice
# fields that cannot hold a Literal.
AGENT_PROVIDER_CHOICES: tuple[str, ...] = get_args(AgentProvider)

# Wire bounds live ON the contract (Field constraints below) so both
# sides enforce one number: the server's serializers import these, the
# web reads them off the generated schema document (maxLength/maxItems
# flow through codegen). Values are binary by house rule.
LABEL_MAX_LENGTH = 128
# A TRANSPORT sanity ceiling only (binary, ~64k tokens): the spend a
# long prompt multiplies is the USER'S OWN key, so cost is never ours
# to cap; the provider's context-window error is the honest signal
# when a prompt truly exceeds a model. This exists to stop
# pathological payloads, not prompt engineering.
PROMPT_MAX_LENGTH = 262_144
MAX_AGENT_OUTPUTS = 8
# The test bench's hand-fed row cap (binary). A test admission
# REFUSES a wider row (never truncates), so the bench cuts to this
# many FILLED inputs in PROMPT order before sending and diagnoses the
# cut (silent excess would render blank prompt variables).
TEST_ROW_MAX_KEYS = 16
# The bench row's per-key and per-value bounds (binary), WIRE facts:
# a test-fill admission REFUSES past them (never truncates), so the
# client must know the bound it can be refused under (bench inputs
# carry it as maxLength, making the refusal unreachable from the UI).
TEST_KEY_MAX_LENGTH = 64
TEST_VALUE_MAX_LENGTH = 512
# Tool calls per cell run. A WIRE fact, not a runtime internal: the
# fill consent footer's "up to N searches" is rows times this number,
# computed client-side off x-constants, so both sides must read one
# home. The runtime derives its own bounds from it. Sized for TWO
# providers: both tools share this one budget when both are on, so a
# budget sized for one starves a run that uses both.
MAX_TOOL_CALLS = 6
# Outputs BECOME sheet columns when a fill maps them: their bounds ARE
# the column bounds, derived so they cannot drift wider (a wider bound
# here would truncate persisted data at the mapping seam).
OUTPUT_KEY_MAX_LENGTH = COLUMN_KEY_MAX_LENGTH
# Output keys containing this marker are refused: on answer models
# every output key gains `<key>_bwr_confidence_reason` and
# `<key>_bwr_confidence` companions, so the namespace is reserved and
# a user asking for their own "Confidence" output stays legal (those
# keys never contain the marker).
RESERVED_OUTPUT_MARKER = "_bwr_"
# The companions themselves. Three places must agree on these names:
# the schema that DECLARES them per output, the validator that pulls
# them back OFF the answer, and the instructions that name them to the
# model. Spelled once, here.
CONFIDENCE_REASON_SUFFIX = f"{RESERVED_OUTPUT_MARKER}confidence_reason"
CONFIDENCE_SUFFIX = f"{RESERVED_OUTPUT_MARKER}confidence"
OUTPUT_LABEL_MAX_LENGTH = COLUMN_LABEL_MAX_LENGTH
OUTPUT_DESCRIPTION_MAX_LENGTH = 256


class AgentTools(BaseModel):
    """The tool REGISTRY, one typed field per tool: the wire itself
    carries the key set, so the web derives its tool list from this
    shape instead of hand-retyping it. Unknown keys are TOLERATED
    (dropped) here because this model validates on every read, and a
    server that adds a tool must not fail stored rows or a browser
    holding the old bundle; the REQUEST leg's serializer is where
    unknown keys refuse, which is what keeps the set closed on the
    way in."""

    web_search: bool = False
    find_contacts: bool = False


class AgentOutput(BaseModel):
    """One declared output: a named, described field the model must
    fill (the description rides into the instruction), landing as one
    cell per row."""

    key: str = Field(
        max_length=OUTPUT_KEY_MAX_LENGTH,
        description="Present always; BLANK allowed on requests (the server derives it from "
        "the label) and always populated on responses (required, no default: a response "
        "omitting it must fail the parse, never invent an empty string).",
    )
    label: str = Field(
        max_length=OUTPUT_LABEL_MAX_LENGTH,
        description="Non-blank on the wire (the server refuses blank labels at the request "
        "boundary); the builder's local draft may hold blank rows, client-side only.",
    )
    type: ColumnType = Field(description="Sheet display type; drives rendering only.")
    description: str = Field(default="", max_length=OUTPUT_DESCRIPTION_MAX_LENGTH)


class AgentConfig(BaseModel):
    """The runtime's interchange unit, shared by both custodies (a
    saved agent, a column's quick prompt) and the test bench."""

    prompt: str = Field(max_length=PROMPT_MAX_LENGTH)
    provider: AgentProvider
    source: str = Field(description="Which server of that spec (the env-named source).")
    model: str
    tools: AgentTools
    outputs: list[AgentOutput] = Field(min_length=1, max_length=MAX_AGENT_OUTPUTS)

    # Named reads for the tool toggles (Python-side convenience only;
    # properties never reach the generated JSON schema).
    @property
    def finds_contacts(self) -> bool:
        return self.tools.find_contacts

    @property
    def searches_web(self) -> bool:
        return self.tools.web_search

    @property
    def uses_tools(self) -> bool:
        # DERIVED over every field, never a hand-written OR: three
        # guards gate on this (the no-spend refusal, the fabrication
        # guard, the free-search budget), and a tool field this misses
        # would switch all three off silently.
        return any(self.tools.model_dump().values())


class AgentSummary(BaseModel):
    """A stored agent (one custody of a config)."""

    id: str
    label: str = Field(max_length=LABEL_MAX_LENGTH)
    config: AgentConfig
    ephemeral: bool = Field(
        description="True for a column-owned quick-prompt agent: hidden from the roster, "
        "excluded from MAX_AGENTS, deleted with its column.",
    )
    created_at: str
    updated_at: str


class AgentListItem(BaseModel):
    """One list row: the index ships what the table renders, never
    each agent's whole config (a full list of maxed prompts would be
    megabytes to draw four columns; the edit page fetches its agent by
    id)."""

    id: str
    label: str = Field(max_length=LABEL_MAX_LENGTH)
    model: str
    tools: AgentTools
    created_at: str
    updated_at: str


class AgentsList(BaseModel):
    items: list[AgentListItem]


class CatalogModel(BaseModel):
    """One runnable model on this deploy."""

    provider: AgentProvider
    source: str
    model: str


# The search seam's vendors, mirrored from the server's provider
# registry (pinned by a parity test). Defined BEFORE AgentCatalog
# uses it, like every other wire alias.
SearchProviderWire = Literal["duckduckgo", "dataforseo"]
# The providers as DATA for the wire document (x-constants): the
# client's tolerant catalog read maps any provider outside this set to
# null instead of failing the whole parse (a strict enum would brick
# the model picker for every deployed bundle the day a third one
# ships).
SEARCH_PROVIDER_CHOICES: tuple[str, ...] = get_args(SearchProviderWire)
# The BASE tool status codes (the server's ToolStatus, pinned): what
# a tool's call did. Each tool's own vocabulary contains these and may
# add its own; the client resolves copy by (tool, code) and tolerates
# a code it has not heard of.
ToolStatusWire = Literal["open", "not_configured", "rate_limited", "unreachable", "error"]
# Each tool's FULL vocabulary (the base codes plus the tool's own),
# shipped as an x-constant so the client types its copy table per tool.
# Mirrors the server enums (agents.constants.SearchStatus), pinned.
SEARCH_STATUSES: tuple[str, ...] = get_args(ToolStatusWire)
TOOL_STATUSES: dict[str, tuple[str, ...]] = {"web_search": SEARCH_STATUSES, "find_contacts": SEARCH_STATUSES}


class AgentCatalog(BaseModel):
    """What THIS deploy can run; `tools` gates the toggles."""

    models: list[CatalogModel]
    support_followup: str = Field(
        description="The deployment's needs-attention follow-up, profile-owned server-side "
        "(check the logs locally; the operator's support channel hosted). Client copy composes "
        "it instead of hedging about an operator it cannot identify."
    )
    truncated: bool = Field(
        description="True when the catalog cap cut the list: an address past the cap "
        "may still RUN (model_for validates against the full roster), it just is not shown."
    )
    tools: dict[str, str] = Field(
        description="Each tool's availability BEFORE a run, keyed by registered tool name "
        "(web_search, find_contacts): 'open' gates the toggle on; any other code is the reason "
        "it is off (today only 'not_configured' can appear here; the run-time codes ride the "
        "cells)."
    )
    search_provider: SearchProviderWire | None = Field(
        description="The vendor serving web search on this deployment, only while it is READY to "
        "serve (registered, wired, credentialed); null otherwise, so client copy never names a "
        "vendor whose searches cannot run. Client copy composes it: a rate-limited cell names the "
        "metered door only where it is a remedy, never to someone already on it."
    )


class SearchToolCall(BaseModel):
    """One search-tool call's record: `status` is what the provider
    said (open, and hits, possibly zero, is the honest answer; any
    other code is the tool's own failure code saying why there are
    none), `provider` which provider served it, `attempts` how many
    tries the seam made for this one query (a rate limit is retried,
    same query, before it counts), and `tool` which tool called, so a
    reader can tell whose call refused. `hits` counts what the tool
    KEPT; `discarded` counts served hits the tool dropped as off-scope
    (an engine that runs dry on a demanded site: relaxes the query and
    serves off-site pages dressed as answers), so a thin answer shows
    whether the provider ran dry or ran off. Every field REQUIRED: the
    one writer sets them all, and a stored record is the same shape as
    a served one."""

    # The record FAMILY discriminant, defaulted so records stored
    # before it read back as what they are. Two search tools share
    # this one member (kind says the SHAPE, `tool` says who called);
    # a differently-shaped record (a contact provider's filters-in,
    # records-out call) joins as a new member with its own kind.
    kind: Literal["search"] = "search"
    tool: str
    status: str
    provider: str
    attempts: int
    query: str
    hits: int
    discarded: int


# The tool-call record family, ONE member today: the seam a new tool's
# record type joins (a second member turns this into a discriminated
# union on `kind`, which is why kind exists now, while adding it is a
# defaulted field instead of a stored-blob migration).
ToolCall = SearchToolCall

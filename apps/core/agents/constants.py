"""Bounds + enums for the agents domain. INVENTED numeric bounds are
binary by house rule; derived values (the worst case, bounds mirrored
from the contract or kernel) state their derivation instead."""

from __future__ import annotations

from enum import StrEnum

from openbower_kernel.provider_config import SOURCE_NAME_MAX_LENGTH, ProviderSpec
from openbower_schema.agents import (
    LABEL_MAX_LENGTH as LABEL_MAX_LENGTH,
)
from openbower_schema.agents import (
    MAX_AGENT_OUTPUTS as MAX_AGENT_OUTPUTS,
)
from openbower_schema.agents import (
    MAX_TOOL_CALLS as MAX_TOOL_CALLS,
)
from openbower_schema.agents import (
    OUTPUT_DESCRIPTION_MAX_LENGTH as OUTPUT_DESCRIPTION_MAX_LENGTH,
)
from openbower_schema.agents import (
    OUTPUT_KEY_MAX_LENGTH as OUTPUT_KEY_MAX_LENGTH,
)
from openbower_schema.agents import (
    OUTPUT_LABEL_MAX_LENGTH as OUTPUT_LABEL_MAX_LENGTH,
)
from openbower_schema.agents import (
    PROMPT_MAX_LENGTH as PROMPT_MAX_LENGTH,
)
from openbower_schema.agents import (
    TEST_ROW_MAX_KEYS as TEST_ROW_MAX_KEYS,
)

# Inference doors are API SPECS, never products (a local Ollama or
# vLLM is the openai_compatible door with its base pointed there;
# keyless bases are OPEN when non-canonical). The enum itself lives in
# the kernel: the config file's vocabulary, the model column, and the
# wire Literal are one fact.
AgentProvider = ProviderSpec

# Wire bounds (label, prompt, outputs) live on the CONTRACT and are
# re-exported here: one number enforced by the serializer, the wire
# models, and the web's generated schema alike.
# A sanity ceiling on forged input ONLY: real values are picker-chosen
# from our own catalog, no provider promises name lengths, and Ollama's
# hf.co pull names are the long family this must clear.
MODEL_MAX_LENGTH = 256
# The env-declared source name (which server of a spec); the kernel
# refuses longer names at parse, so an address the catalog offers is
# always one the serializer accepts.
SOURCE_MAX_LENGTH = SOURCE_NAME_MAX_LENGTH
# The roster is unpaged, so a bound must exist; counts ephemeral=False
# rows only (ephemeral rows are bounded by the columns that own them).
MAX_AGENTS = 128
# The catalog is UNPAGED under the keyset rule's hard-cap carve-out:
# this constant is the cap (binary, far above any real deploy; an
# Ollama host with hundreds of models still fits), and the registry
# logs when it truncates.
CATALOG_MAX_MODELS = 512
# Roster probes fan out concurrently (binary): a cold catalog costs
# the SLOWEST source's probe, never the sum of every source's timeout.
CATALOG_PROBE_CONCURRENCY = 8

# MAX_TOOL_CALLS (imported above): the agentic loop's per-cell tool
# budget. Lives on the CONTRACT since the fill consent footer computes
# "up to N searches" from it client-side; the runtime's derivations
# read the re-export here.
# Column widths for enum-backed fields (generous over exact).
PROVIDER_MAX_LENGTH = 32
STATUS_MAX_LENGTH = 16
# Search queries are MODEL-AUTHORED text crossing into a metered
# external call: bounded, like every authored value here (Google
# ignores everything past ~32 words anyway, so truncation loses no
# recall).
QUERY_MAX_LENGTH = 256
# Hits pooled per query (binary): the evidence a single search may
# contribute, at a metered boundary.
SEARCH_HIT_COUNT = 8
# The pool's own ceiling, DERIVED so it cannot fall behind the tool
# budget: every completion re-reads the pool, so its size is a token
# cost per call, but a ceiling under the budget silently discards the
# late searches the budget was raised to buy.
EVIDENCE_MAX_LINES = MAX_TOOL_CALLS * SEARCH_HIT_COUNT
# Test-row keys mirror prompt tokens; bounded like every authored value.
TEST_KEY_MAX_LENGTH = 64
# The confidence floor: an answer whose model-stated confidence sits
# below this is discarded per-field (the blank reads unverified).
# Confident-or-blank is the product's contract; 0.9 keeps only answers
# the model itself would stake the row on.
#
# NEVER stated to the model. A named threshold is a target: a model
# told the bar reports the bar, and the score stops measuring anything.
# It also freezes the number, since a floor can only be tuned against
# scores that were not anchored to it (the dropped values and their
# scores persist on the outcome row for exactly that).
ANSWER_CONFIDENCE_FLOOR = 0.9
# Timeouts (binary): local models are slow to first token, a roster
# probe is quick or dead, the free SERP answers fast or not at all,
# and DataForSEO's live endpoint computes per request (10-20s
# routinely, spikes beyond).
COMPLETION_TIMEOUT_SECONDS = 128
LIST_TIMEOUT_SECONDS = 16
# A failed roster probe retries only after this (binary): the
# boot-race must heal without a restart, but a dead source must not
# cost a serial probe timeout on EVERY request meanwhile.
PROBE_FAILURE_TTL_SECONDS = 16
SEARCH_TIMEOUT_SECONDS = 16
DATAFORSEO_TIMEOUT_SECONDS = 64
# A rate-limited query is retried, SAME query, on this schedule (binary,
# 15s of waiting at most) before the seam gives up on it. Transport,
# never the model's budget: a retry of one question is not a new one.
SEARCH_BACKOFF_SECONDS = (1, 2, 4, 8)
# One validation retry per run: the framework re-asks once on an
# invalid output, then None is signal.
MODEL_RETRIES = 1
# The completion token cap is SCHEMA-DERIVED (base + per-output): a
# fixed cap plus a typed output would be a silent failure mode (the
# provider CUTS generation at the ceiling and the truncated answer
# fails validation, blanking every row of a wide agent). A cap is a
# maximum, not a target; providers bill only generated tokens. The
# per-output figure carries the confidence pair as well as the answer,
# so it is sized for three fields per output, not one.
COMPLETION_TOKENS_BASE = 256
COMPLETION_TOKENS_PER_OUTPUT = 256

# Test bench bounds: hand-fed fixture values, not cells
# (TEST_ROW_MAX_KEYS lives on the contract; the bench reads it too).
TEST_VALUE_MAX_LENGTH = 512
# A failed run's wire diagnosis, clamped like every authored value.
TEST_RUN_ERROR_MAX_LENGTH = 256
# The runtime's worst case for ONE run, derived, never invented: every
# completion the request budget allows at the completion timeout, plus
# every paid search at its own timeout and its full backoff schedule
# (the free door's timeout is shorter, so the paid door's bounds both).
# The wire's poll_budget_seconds
# publishes THIS (a hung run must not spin the client for the whole
# stale window).
TEST_RUN_WORST_CASE_SECONDS = (MAX_TOOL_CALLS + 3) * COMPLETION_TIMEOUT_SECONDS + MAX_TOOL_CALLS * (
    DATAFORSEO_TIMEOUT_SECONDS + sum(SEARCH_BACKOFF_SECONDS)
)
# A pending run is superseded only after this much SILENCE since its
# last poll (the poll GET stamps polled_at). Sized ABOVE browser
# background-tab throttling (a hidden tab's timers drop to about one
# fire per minute), or a legitimately running test in a backgrounded
# tab would read as abandoned and be superseded mid-spend; still far
# under the worst case. Run AGE says nothing here: one completion
# timeout alone is this long.
TEST_RUN_ABANDON_SECONDS = 128
# Concurrent test-run threads PER PROCESS (binary): the account-wide
# invariant lives in the start guard; this is the local backstop for
# the paid work itself, REFUSING (a fast failed run with its why)
# rather than queueing, because queue time is invisible to the
# published poll budget.
TEST_RUN_MAX_CONCURRENT = 2
# A run still pending past this is ORPHANED (daemon threads die
# unwound on restarts); the poll leg presents it as failed. Binary,
# and strictly above TEST_RUN_WORST_CASE_SECONDS (pinned) so a
# legitimately slow run is never presented dead.
TEST_RUN_STALE_PENDING_SECONDS = 2_048
# Finished test runs are throwaway diagnostics; anything older than
# this purges opportunistically on the next test POST.
TEST_RUN_MAX_AGE_SECONDS = 4_096

# The people-profile site find_contacts pins its queries to (the tool
# injects the site: scope; the model never controls it).
DEFAULT_PEOPLE_SITE = "linkedin.com/in"


class TestRunStatus(StrEnum):
    """A test-bench run's lifecycle (polled: the bench must not hold a
    connection for the seconds a run takes)."""

    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class SearchProvider(StrEnum):
    """The search seam's doors: the free keyless default, and the paid
    Google-grade door contact search pins to. Settings mirror these
    values as literals (settings cannot import app code); a parity
    test pins the mirror."""

    DUCKDUCKGO = "duckduckgo"
    DATAFORSEO = "dataforseo"


class ToolStatus(StrEnum):
    """The BASE status codes every tool can report for one call: what
    its door did. Each outcome type carries its own enum that restates
    these (StrEnums cannot extend one another; a parity test pins the
    containment) and may add modes of its own, so a tool-specific
    failure never lands here and never touches another tool. The
    sheet's cell vocabulary is a table keyed by code (lists:
    CELL_STATE_BY_STATUS), so a tool-specific code adds one row there;
    the tool's own code rides the task and the cell record beside the
    cell state."""

    OPEN = "open"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    ERROR = "error"


class SearchStatus(StrEnum):
    """The search door's vocabulary: the base codes, plus any mode only
    a search door has (none yet)."""

    OPEN = "open"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    ERROR = "error"


# The search statuses that CLOSE the tool's door for the rest of the
# run, the moment they are reported: a rate limit (the seam already
# retried it) and a door found unconfigured. Unreachable and error do
# not: the next query may get through, and a door that only ever
# failed is settled at the end of the run instead.
SEARCH_DOOR_CLOSERS = frozenset({SearchStatus.RATE_LIMITED, SearchStatus.NOT_CONFIGURED})


class AgentTool(StrEnum):
    """The tools the model may drive (each gated by its search door).
    FIND_CONTACTS is the people x-ray, pinned to DataForSEO;
    WEB_SEARCH runs the configured door."""

    WEB_SEARCH = "web_search"
    FIND_CONTACTS = "find_contacts"

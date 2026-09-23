"""Bounds + enums for the agents domain. INVENTED numeric bounds are
binary by house rule; derived values (the worst case, bounds mirrored
from the contract or kernel) state their derivation instead."""

from __future__ import annotations

from enum import StrEnum

from openbower_kernel.provider_config import SOURCE_NAME_MAX_LENGTH
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
# Column width for the stored provider name, and the bound the
# registry holds a registered (module-derived) name to: a name past
# it could never be stored (generous over exact).
PROVIDER_MAX_LENGTH = 32
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
# probe is quick or dead, and a SERP answers fast or not at all.
COMPLETION_TIMEOUT_SECONDS = 128
LIST_TIMEOUT_SECONDS = 16
# A failed roster probe retries only after this (binary): the
# boot-race must heal without a restart, but a dead source must not
# cost a serial probe timeout on EVERY request meanwhile.
PROBE_FAILURE_TTL_SECONDS = 16
# The GENERAL search-call budget, quick or dead: the default for every
# vendor's transport timeout (ProviderSpec.timeout_seconds). A vendor
# that computes per request declares its own bigger number ON ITS
# SPEC, in its own module, never here.
SEARCH_TIMEOUT_SECONDS = 16
# A rate-limited query is retried, SAME query, on this schedule
# (binary) before the seam gives up on it. Transport, never the
# model's budget: a retry of one question is not a new one. The
# schedule's own waits total 15s, but a provider's Retry-After ask is
# honored clamped to the LARGEST step, so the true bound per call is
# len(schedule) * max(schedule) (search_attempt_worst_case_seconds
# carries it into the worst-case derivation).
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

# The capped-verdict fallback is a SECOND agent run with its own
# request budget (answer.py's UsageLimitExceeded leg): one verdict
# call plus the framework's validation retries, each a completion at
# the full timeout.
CAPPED_VERDICT_REQUESTS = 1 + MODEL_RETRIES


def search_attempt_worst_case_seconds() -> int:
    """One search call's worst case: the slowest REGISTERED vendor's
    declared timeout, plus every wait the backoff schedule allows AT
    ITS CLAMP. Not sum(schedule): a provider answering Retry-After
    above the schedule is honored clamped to the schedule's largest
    step on EVERY attempt, so each wait can reach max(schedule), not
    its own step. A CALLED accessor over the registry (which fills at
    ready(), after this module imports), so registering a slower
    vendor raises the bound by itself; nothing here to remember."""
    from .tools.search.providers.registry import all_providers

    return max(p.timeout_seconds for p in all_providers()) + len(SEARCH_BACKOFF_SECONDS) * max(SEARCH_BACKOFF_SECONDS)


def cell_run_worst_case_seconds() -> int:
    """The runtime's worst case for ONE run, derived, never invented:
    every completion the request budget allows at the completion
    timeout, including the capped verdict's own budget, plus every
    search at its clamped worst case. The NORMAL fill worker's compose
    stop_grace_period must clear it (a row that runs to the bound
    still owes its outcome write); the test lane's worker trades that
    away deliberately, so its short grace kills an in-flight preview
    row. Called, like the search bound it composes."""
    return (
        MAX_TOOL_CALLS + 3 + CAPPED_VERDICT_REQUESTS
    ) * COMPLETION_TIMEOUT_SECONDS + MAX_TOOL_CALLS * search_attempt_worst_case_seconds()


# The people-profile site find_contacts pins its queries to (the tool
# injects the site: scope; the model never controls it).
DEFAULT_PEOPLE_SITE = "linkedin.com/in"


class ToolStatus(StrEnum):
    """The BASE status codes a provider-backed tool reports for one
    call. A tool's failure vocabulary is its spec's failure_modes KEYS
    (tool-owned, open vocabulary); the sheet's cell vocabulary maps
    from each code's declared failure MODE in the runtime's harness,
    and the tool's own code rides the task and the cell record beside
    the cell state."""

    OPEN = "open"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    ERROR = "error"


# The search family's vocabulary IS the base today (no search-only
# code exists): an ALIAS, not a twin, so the two spellings cannot
# drift. The day the family adds its own code this becomes a real
# enum (the base members plus the new one), and the parity pins
# (declared codes are members; members ship on the wire) catch every
# seam that has to follow.
SearchStatus = ToolStatus

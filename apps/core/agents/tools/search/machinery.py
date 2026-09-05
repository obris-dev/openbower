"""The shared machinery for SEARCH-PROVIDER-BACKED tools, wherever
their family package lives (web search here, the contacts x-ray next
door). Such a tool composes its spec from the pieces here (the shared
failure modes, the availability and failure-copy builders) and its
function calls `run_search`; a tool that talks to something other
than a search provider needs none of this and builds against `base`
alone.

A provider's status is PER TOOL within the run: one tool's provider
refusing never refuses another, and never discards an answer another
tool grounded. What each provider said rides out of the run as data
(the outcomes and the final status per tool) for the fill operation to
record on the task and the cell."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from ...constants import EVIDENCE_MAX_LINES, SearchStatus, ToolStatus
from ...runtime.deps import CellDeps, EvidenceRecord
from ...runtime.grounding import canonical_url
from ...runtime.outcomes import SearchOutcome
from ..base import FailureCode, FailureCopy, ToolSpec, result_json
from .errors import SearchToolError
from .providers.base import SearchHit
from .providers.registry import get, provider_status
from .providers.schedule import search

logger = logging.getLogger(__name__)

# What a provider's status reads as in a failure sentence (the breaker's
# tier-1 copy quotes it through each spec's failure_copy).
STATUS_PHRASE = {
    ToolStatus.NOT_CONFIGURED: "isn't set up on",
    ToolStatus.RATE_LIMITED: "is being rate-limited by",
    ToolStatus.UNREACHABLE: "cannot reach",
    ToolStatus.ERROR: "is failing on",
}


def serving_provider(tool_name: str, vendors: tuple[str, ...]) -> str:
    """The ONE resolution rule for which vendor serves a family tool:
    the deploy's wiring for the tool, else the tool's declared roster
    head, read at call time (a setting, so never frozen into a spec
    built at import). `or`, not get(default): an empty wiring value
    must fall back, not name nothing."""
    return settings.TOOL_WIRING.get(tool_name) or vendors[0]


def provider_availability(tool_name: str, vendors: tuple[str, ...]) -> Callable[[], SearchStatus]:
    """The availability of a provider-backed tool: what the search seam
    says, before any call, about the vendor that would serve it
    (resolved by the same rule every call uses)."""
    return lambda: provider_status(serving_provider(tool_name, vendors))


def serving_display(tool_name: str, vendors: tuple[str, ...]) -> str:
    """The serving vendor's name as failure copy prints it: the
    declared display when it is registered, the raw wiring value when
    it is not (a misconfigured deploy still deserves a sentence that
    names what it asked for)."""
    name = serving_provider(tool_name, vendors)
    try:
        return get(name).display
    except KeyError:
        return name


def provider_failure_copy(
    display_name: str, provider_name: Callable[[], str], remedy: Callable[[], str]
) -> Callable[[FailureCode], FailureCopy]:
    """The failure_copy of a provider-backed tool, composed from the
    STATUS_PHRASE table. `provider_name` and `remedy` are callables for
    the same reason provider_availability's provider is: which provider
    serves, and whether a remedy exists, can depend on settings read at
    failure time."""

    def copy(code: FailureCode) -> FailureCopy:
        # Direct lookup, loudly: the parity suite pins every declared
        # code to a phrase, so a miss is a broken contract, never a
        # sentence to default around.
        return FailureCopy(problem=f"{display_name} {STATUS_PHRASE[code]} {provider_name()}", remedy=remedy())

    return copy


@dataclass(frozen=True, kw_only=True)
class SearchToolSpec(ToolSpec):
    """The FAMILY spec: everything the base contract asks, plus the
    facts only a search-provider-backed tool has. Which vendors may
    serve is SPEC data (the declared roster below; WHICH of them does
    is the deploy's wiring, a setting read at call time), and a
    DEMANDED scope travels here too, so `run_search` serves every
    family tool and a scoped tool never forks the shared path to
    carry its own facts.

    The base contract's two runtime callables DERIVE from the
    declared facts at construction, so a family tool passes its
    facts once: availability asks the seam about the vendor serving
    THIS tool, and failure_copy names that vendor with an empty
    remedy (a roster with no free door has no cheaper next step to
    offer). A tool whose copy says more declares its own, as web
    search's metered-door nudge does."""

    # Redeclared with defaults so the family may derive them; the
    # registry still refuses a spec where neither declaration nor
    # derivation produced a callable.
    availability: Callable[[], SearchStatus] | None = None
    failure_copy: Callable[[FailureCode], FailureCopy] | None = None

    # The vendors that may serve this tool, in order, the head being
    # the default when the deploy's wiring names none. DECLARED, next
    # to the tool: a vendor joins a roster when its results are known
    # to work for this tool's job, and the [tools] wiring is held to
    # the roster at boot. Registry MEMBERSHIP is the boot gate's
    # check, not construction's: vendors register in the same ready()
    # walk as the tools themselves.
    vendors: tuple[str, ...] = ()
    # Look at one potential result and say yes or no: the tool's own
    # test for whether a served hit can answer AT ALL, any criterion
    # over the hit. A rejected hit never pools as evidence and the
    # audit counts it as discarded. The shipped use is a demanded
    # site: scope (scope_test below derives the URL check from the
    # same pattern the query carries; a rejected hit there is the
    # engine relaxing the query). None = every served hit may pool;
    # relevance stays the model's judgment.
    check_hit: Callable[[SearchHit], bool] | None = None
    # The model-facing note for a call the provider SERVED but whose
    # every hit the check rejected: declared WITH the check, since an
    # emptied pool must say why.
    rejected_note: str = ""

    def __post_init__(self) -> None:
        # Family completeness at construction, the same loud-early
        # rule the registry applies to the base contract: every family
        # tool has a roster by construction, and the derivations below
        # read it.
        if not self.vendors or not all(isinstance(v, str) and v.isidentifier() for v in self.vendors):
            raise ValueError(f"search tool {self.name!r} must declare a vendors roster of identifier names")
        super().__post_init__()
        if self.availability is None:
            object.__setattr__(self, "availability", provider_availability(self.name, self.vendors))
        if self.failure_copy is None:
            object.__setattr__(
                self,
                "failure_copy",
                provider_failure_copy(self.display_name, lambda: serving_display(self.name, self.vendors), lambda: ""),
            )
        if (self.check_hit is None) != (self.rejected_note == ""):
            raise ValueError(f"search tool {self.name!r} must declare check_hit and rejected_note together")


def scope_test(scope: str) -> Callable[[SearchHit], bool]:
    """A check_hit BUILDER for the common case, a demanded site: scope:
    derives the URL test from the same pattern the query carries, so
    the fact is stated once. The host must be the scope's domain or a
    subdomain of it (the leading dot is the spoof guard: without it, a
    lookalike suffix domain would pass), and the path must sit under
    the scope's path when it declares one."""
    domain, _, prefix = scope.partition("/")

    def check(hit: SearchHit) -> bool:
        parts = urlsplit(hit.url)
        host = parts.hostname or ""
        if host != domain and not host.endswith(f".{domain}"):
            return False
        return parts.path.startswith(f"/{prefix}/") if prefix else True

    return check


def run_search(deps: CellDeps, query: str, *, spec: SearchToolSpec) -> str:
    """ONE search for a family tool, entirely off its SPEC: ask the
    spec's provider (ask_provider records the audit and raises the
    family's typed error the moment a failure is decided), enforce
    the spec's demanded scope, and pool the kept hits into the run's
    EVIDENCE (deps.records, the numbered records every completion
    re-reads and grounding fences the answer to), handing the model
    the ones this call added. The harness owns the closed-tool
    refusal BEFORE any spend, so the stored calls record only what
    hit the wire."""
    outcome = ask_provider(
        deps, query, spec=spec, provider=serving_provider(spec.name, spec.vendors), check_hit=spec.check_hit
    )
    if not outcome.hits and outcome.discarded:
        return result_json([], spec.rejected_note)
    return result_json(pool_hits(deps, outcome.hits, label=spec.record_label))


def ask_provider(
    deps: CellDeps,
    query: str,
    *,
    spec: ToolSpec,
    provider: str,
    check_hit: Callable[[SearchHit], bool] | None = None,
) -> SearchOutcome:
    """The spend: the provider call, timed, with everything it said
    recorded on deps for the audit. The provider HARNESS raises the family's
    typed error at the moment a failure is decided (post-retry),
    carrying the audit facts; this catches ONCE to append the failed
    call's record and re-raises for the harness to fold. A success
    appends its record and folds the SERVE (the tool alone can tell a
    served call from a no-op return, and served is the blame
    exemption); the failure fold is the harness's, from the raise,
    with the same verb and the same rules. Every path records once
    and folds once.

    `check_hit` is the tool's own per-hit test, enforced on the way
    back (the shipped case: a demanded site: scope an engine under
    degradation relaxes away, serving off-scope pages as if they
    answered). The outcome's hits are what the run may pool; each
    rejected hit rides `discarded` for the audit."""
    started = time.monotonic()
    try:
        answer = search(query, provider=provider)
    except SearchToolError as failure:
        deps.outcomes.append(
            SearchOutcome(
                tool=spec.name,
                status=SearchStatus(failure.code),
                provider=failure.provider,
                attempts=failure.attempts,
                query=query,
                hits=[],
            )
        )
        raise
    finally:
        deps.add_call_seconds(spec.name, time.monotonic() - started)
    hits = answer.hits if check_hit is None else [hit for hit in answer.hits if check_hit(hit)]
    discarded = len(answer.hits) - len(hits)
    if discarded:
        logger.info("%s discarded %d off-scope hit(s) (%s): %r", spec.name, discarded, answer.provider, query[:120])
    outcome = SearchOutcome(
        tool=spec.name,
        status=SearchStatus.OPEN,
        provider=answer.provider,
        attempts=answer.attempts,
        query=query,
        hits=hits,
        discarded=discarded,
    )
    deps.outcomes.append(outcome)
    deps.record_tool_status(spec.name, ToolStatus.OPEN, spec.closers)
    return outcome


def pool_hits(deps: CellDeps, hits: list[SearchHit], *, label: str) -> list[dict]:
    """The evidence pool: each new hit becomes a numbered record the
    model, the validator, and the stored evidence all share."""
    pooled = []
    for hit in hits:
        # Canonical dedupe: www./regional variants of one page pool
        # once (grounding compares canonically too).
        key = canonical_url(hit.url)
        if key in deps.seen:
            continue
        if len(deps.records) >= EVIDENCE_MAX_LINES:
            # A NAMED ceiling instead of an implicit one: the pool is
            # what every completion re-reads, so its size is a cost.
            break
        deps.seen.add(key)
        # Numbered ACROSS calls: the number is the citation key the
        # model, the validator, and the stored evidence all share.
        record = EvidenceRecord(
            position=len(deps.records) + 1,
            tool=label,
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
        )
        deps.records.append(record)
        pooled.append(record.as_json())
    return pooled


__all__ = [
    "STATUS_PHRASE",
    "SearchToolSpec",
    "ask_provider",
    "pool_hits",
    "provider_availability",
    "provider_failure_copy",
    "run_search",
    "serving_display",
    "serving_provider",
]

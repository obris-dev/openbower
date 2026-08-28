"""The search seam: `search(query) -> SearchOutcome` behind a provider
switch (settings.SEARCH_PROVIDER).

TWO doors: "duckduckgo" (the DEFAULT: free, keyless, zero setup via
the ddgs library, so web search works out of the box and offloads the
paid door) and "dataforseo" (pay-as-you-go Google SERPs on a
non-expiring balance, $1 trial credit; DATAFORSEO_LOGIN + PASSWORD;
the live endpoint at ~$2/1k; the door contact search PINS regardless
of this switch). An explicitly chosen door missing its credentials is
NOT AVAILABLE: tools gate off in the UI and searches skip honestly.
The seam stays swappable underneath if a provider ever needs to
change.

Transport is the seam's, never the model's: a rate limit is retried
here, SAME query, on a bounded schedule, and the outcome says which
door served it, why it failed, and how many tries it took. Each door
classifies its own refusals, because the doors speak differently.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import NamedTuple

import httpx
from ddgs.engines.duckduckgo import Duckduckgo
from ddgs.exceptions import TimeoutException as DDGSTimeout
from django.conf import settings

from openbower_schema.agents import TestSearch

from .constants import (
    DATAFORSEO_TIMEOUT_SECONDS,
    SEARCH_BACKOFF_SECONDS,
    SEARCH_HIT_COUNT,
    SEARCH_TIMEOUT_SECONDS,
    SearchFailure,
    SearchProvider,
)

logger = logging.getLogger(__name__)

# Sleeping is a module seam so tests assert the schedule instead of
# waiting it out.
_sleep = time.sleep


class SearchMisconfigured(Exception):
    """An unusable provider reached the seam: a CONFIG error (callers
    gate on availability), never a per-query hazard to swallow."""


class SearchRateLimited(Exception):
    """A door said slow down. `retry_after` is the door's own ask in
    seconds when it made one (DataForSEO's header); the seam honors it
    clamped to the schedule's longest step, and takes the schedule's
    step otherwise."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SearchTimedOut(Exception):
    """A door said nothing in time. Raised by doors whose transport
    does not speak httpx (the paid door's httpx timeout is caught by
    its own type)."""


class SearchHit(NamedTuple):
    title: str
    url: str
    snippet: str


class SearchOutcome(NamedTuple):
    """One query's result AND its diagnosis: `failed` means the provider
    errored, distinct from an honest zero-hit answer, and `cause` says
    why (a SearchFailure value, "" when clean). `provider` is the door
    that served it and `attempts` how many tries this one call made
    (1 when clean), so the stored audit shows what actually hit the
    wire. Cells stay blank either way; the DIAGNOSTICS must not be
    swallowed with the failure, or a throttled provider reads as a bad
    agent."""

    query: str
    hits: list[SearchHit]
    failed: bool
    cause: str = ""
    provider: str = ""
    attempts: int = 1
    # Which TOOL asked (an AgentTool value), stamped by the runtime's
    # pool: the seam serves doors, but the audit a user reads is by
    # tool.
    tool: str = ""

    def wire(self) -> TestSearch:
        """The ONE constructor for the stored and served diagnosis, so
        the bench's writer and the fill worker's cannot drift."""
        return TestSearch(
            query=self.query,
            hits=len(self.hits),
            failed=self.failed,
            cause=self.cause,
            provider=self.provider,
            attempts=self.attempts,
            tool=self.tool,
        )


def _usable(provider: str) -> bool:
    """USABLE, not merely named: a provider with no credentials reads
    as no search, never as a broken agent. Usability is DECLARED per
    door in _DOORS, so a new provider cannot fall through to
    keyless-by-default."""
    door = _DOORS.get(provider)
    return door is not None and door.usable()


def search_available() -> bool:
    """Gates the web_search tool: the CONFIGURED door, whatever it is
    (a weaker one is the operator's tradeoff, just weaker results)."""
    return _usable(settings.SEARCH_PROVIDER)


def contacts_available() -> bool:
    """Gates find_contacts: DataForSEO's credentials, REGARDLESS of the
    web-search door. The LinkedIn x-rays need Google-grade
    SERPs, so contacts route straight through dataforseo whenever its
    credentials exist, whatever door serves the cheap web searches."""
    return _usable(SearchProvider.DATAFORSEO)


def search(query: str, *, count: int = SEARCH_HIT_COUNT, provider: str = "") -> SearchOutcome:
    """One query's SERP outcome; hits [] on any failure (a cell without
    evidence stays empty, an outage never fails a fill). A rate limit
    is retried, SAME query, once per step of SEARCH_BACKOFF_SECONDS;
    rephrasing is the model's decision, never the seam's. A timeout or
    any other error is a per-query hazard: reported once, not retried.
    `provider` overrides the configured door for callers that require
    a specific one (find_contacts pins dataforseo). RAISES
    SearchMisconfigured on an unusable door: availability gates keep
    the runtime away from here, so arriving anyway is a config error,
    not a hazard."""
    provider = provider or settings.SEARCH_PROVIDER
    if not _usable(provider):
        raise SearchMisconfigured(f"search provider {provider!r} is unknown or missing credentials")
    door = _DOORS[provider]
    # One more try than there are pauses: the schedule is the waits
    # BETWEEN tries.
    for attempt, pause in enumerate((*SEARCH_BACKOFF_SECONDS, None), start=1):
        try:
            hits = door.run(query, count)
        except SearchRateLimited as e:
            if pause is None:
                logger.warning("search rate limited after %d tries (%s): %s", attempt, provider, e)
                return SearchOutcome(
                    query, [], failed=True, cause=SearchFailure.RATE_LIMITED, provider=provider, attempts=attempt
                )
            wait = min(e.retry_after, max(SEARCH_BACKOFF_SECONDS)) if e.retry_after is not None else pause
            logger.info("search rate limited (%s); retrying the same query in %ss", provider, wait)
            _sleep(wait)
            continue
        except (SearchTimedOut, httpx.TimeoutException) as e:
            logger.warning("search timed out (%s): %s", provider, e)
            return SearchOutcome(
                query, [], failed=True, cause=SearchFailure.TIMEOUT, provider=provider, attempts=attempt
            )
        except Exception as e:
            # Every other failure is one flag on purpose: a bad payload,
            # a drained balance, and an outage all mean no evidence for
            # THIS query, and none of them asks for a retry.
            logger.warning("search failed (%s: %s): %s", provider, type(e).__name__, e)
            return SearchOutcome(query, [], failed=True, cause=SearchFailure.ERROR, provider=provider, attempts=attempt)
        logger.info("search %r -> %d hits (%s, %d tries)", query[:120], len(hits), provider, attempt)
        return SearchOutcome(query, hits, failed=False, provider=provider, attempts=attempt)
    raise AssertionError("unreachable: the last schedule step returns")


# The free door is DuckDuckGo's OWN engine, called through the ddgs
# library's engine class rather than its aggregator. The aggregator
# fans a query out to a dozen scrapers, drops every engine that
# refuses (it reads any non-200 as "no results"), and answers from
# whichever is left, so under partial throttling it silently swaps
# indexes and an honest-looking empty can mean "Yahoo has not indexed
# it". The engine class exposes the raw status, which is the one fact
# a rate limit needs: html.duckduckgo.com answers a bot challenge as
# 202 (a page with no results in it), and a plain refusal as 403, 429,
# or 503. A 200 with nothing in it is an honest empty.
_DUCKDUCKGO_REFUSALS = frozenset({202, 403, 429, 503})
_DUCKDUCKGO_REGION = "us-en"
_DUCKDUCKGO_SAFESEARCH = "moderate"


class _DuckduckgoPage(NamedTuple):
    """One results page as the engine answered it: the status the
    library would have thrown away, and the hits parsed off a 200
    (empty on any other status)."""

    status_code: int
    hits: list[SearchHit]


def _duckduckgo_fetch(query: str) -> _DuckduckgoPage:
    """The seam's one library touch: the engine's own request path
    (its headers and TLS shape included) and its own parser, with the
    status kept. Tests script pages, not clients."""
    engine = Duckduckgo(timeout=SEARCH_TIMEOUT_SECONDS)
    payload = engine.build_payload(
        query=query, region=_DUCKDUCKGO_REGION, safesearch=_DUCKDUCKGO_SAFESEARCH, timelimit=None
    )
    try:
        response = engine.http_client.request(engine.search_method, engine.search_url, data=payload)
    finally:
        engine.http_client.client.close()
    if response.status_code != 200:
        return _DuckduckgoPage(response.status_code, [])
    results = engine.post_extract_results(engine.extract_results(response.text))
    return _DuckduckgoPage(200, [SearchHit(title=r.title, url=r.href, snippet=r.body) for r in results])


def _duckduckgo(query: str, count: int) -> list[SearchHit]:
    try:
        page = _duckduckgo_fetch(query)
    except DDGSTimeout as e:
        raise SearchTimedOut(str(e)) from e
    if page.status_code in _DUCKDUCKGO_REFUSALS:
        raise SearchRateLimited(f"duckduckgo returned {page.status_code}")
    if page.status_code != 200:
        raise ValueError(f"duckduckgo returned {page.status_code}")
    return page.hits[:count]


# DataForSEO's wire vocabulary (theirs, never ours to rename): only
# ORGANIC results are evidence; ads, answer boxes, and packs are not
# pages a claim can ground to. Searches pin US English for now (a
# locale knob is a future product decision, not an accident).
_DATAFORSEO_ORGANIC = "organic"
_DATAFORSEO_TASK_OK = 20000
# "No Search Results": the provider's honest-empty status, success-shaped.
DATAFORSEO_NO_RESULTS = 40102
# "Internal SE Server Error": the provider's own upstream failed, a
# documented transient (measured at ~20% of searches during a degraded
# window) that asks for exactly what a rate limit asks for: the same
# query again, a little later.
DATAFORSEO_SE_ERROR = 40101
# Their billing floor: depths below 10 cost the same 10.
_DATAFORSEO_DEPTH_FLOOR = 10
_DATAFORSEO_LANGUAGE = "en"
_DATAFORSEO_LOCATION_US = 2840


def _retry_after(response: httpx.Response) -> float | None:
    """The header's delay-seconds form only; the HTTP-date form is
    rare enough on this door that it takes the schedule's step."""
    value = response.headers.get("Retry-After", "")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _dataforseo(query: str, count: int) -> list[SearchHit]:
    response = httpx.post(
        "https://api.dataforseo.com/v3/serp/google/organic/live/regular",
        json=[
            {
                "keyword": query,
                "language_code": _DATAFORSEO_LANGUAGE,
                "location_code": _DATAFORSEO_LOCATION_US,
                "depth": max(count, _DATAFORSEO_DEPTH_FLOOR),
            }
        ],
        auth=(settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD),
        timeout=DATAFORSEO_TIMEOUT_SECONDS,
    )
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        raise SearchRateLimited("dataforseo returned 429", retry_after=_retry_after(response))
    if response.status_code != 200:
        raise ValueError(f"dataforseo returned {response.status_code}")
    tasks = response.json().get("tasks") or []
    task = tasks[0] if tasks else {}
    status = task.get("status_code")
    if status == DATAFORSEO_NO_RESULTS:
        # 40102 IS the answer, not an error: the query matched nothing.
        # Read as failure it made the model burn its tool budget
        # re-asking variants of a question with no answer.
        return []
    if status == DATAFORSEO_SE_ERROR:
        raise SearchRateLimited(f"dataforseo task returned {status}: {task.get('status_message')}")
    if status != _DATAFORSEO_TASK_OK:
        # A 200 envelope can carry a failed TASK (insufficient balance
        # is the likely paid-door failure); reading it as an honest
        # zero-hit drought is exactly the misdiagnosis the failed flag
        # exists to prevent.
        raise ValueError(f"dataforseo task returned {status}: {task.get('status_message')}")
    results = (task.get("result") or [{}])[0].get("items") or []
    return [
        SearchHit(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            snippet=str(r.get("description", "") or ""),
        )
        for r in results
        if r.get("type") == _DATAFORSEO_ORGANIC
    ][:count]


class _Door(NamedTuple):
    """One search door: its live call AND its usability, declared
    together so registering a provider forces both questions. A door's
    `run` raises SearchRateLimited for the refusals the seam should
    retry and SearchTimedOut (or httpx's timeout) for silence; anything
    else it raises is a per-query error."""

    run: Callable[[str, int], list[SearchHit]]
    usable: Callable[[], bool]


# The ONE door enumeration: gate, dispatch, and credential rules can
# never drift apart.
_DOORS: dict[SearchProvider, _Door] = {
    # Keyless by design; the public endpoint needs nothing.
    SearchProvider.DUCKDUCKGO: _Door(run=_duckduckgo, usable=lambda: True),
    SearchProvider.DATAFORSEO: _Door(
        run=_dataforseo,
        usable=lambda: bool(settings.DATAFORSEO_LOGIN and settings.DATAFORSEO_PASSWORD),
    ),
}

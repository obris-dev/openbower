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
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import NamedTuple

import httpx
from ddgs import DDGS
from django.conf import settings

from .constants import DATAFORSEO_TIMEOUT_SECONDS, SEARCH_HIT_COUNT, SEARCH_TIMEOUT_SECONDS, SearchProvider

logger = logging.getLogger(__name__)


class SearchMisconfigured(Exception):
    """An unusable provider reached the seam: a CONFIG error (callers
    gate on availability), never a per-query hazard to swallow."""


class SearchHit(NamedTuple):
    title: str
    url: str
    snippet: str


class SearchOutcome(NamedTuple):
    """One query's result AND its diagnosis: `failed` means the provider
    errored (timeout, non-200, rate limit), distinct from an honest
    zero-hit answer. Cells stay blank either way; the DIAGNOSTICS must
    not be swallowed with the failure, or a throttled provider reads as
    a bad agent."""

    query: str
    hits: list[SearchHit]
    failed: bool


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
    evidence stays empty, an outage never fails a fill). `provider`
    overrides the configured door for callers that require a specific
    one (find_contacts pins dataforseo). RAISES SearchMisconfigured on
    an unusable door: availability gates keep the runtime away from
    here, so arriving anyway is a config error, not a hazard."""
    provider = provider or settings.SEARCH_PROVIDER
    if not _usable(provider):
        raise SearchMisconfigured(f"search provider {provider!r} is unknown or missing credentials")
    try:
        hits = _DOORS[provider].run(query, count)
    except Exception as e:  # any provider trouble = no evidence
        logger.warning("search failed (%s): %s", type(e).__name__, e)
        return SearchOutcome(query, [], failed=True)
    logger.info("search %r -> %d hits (%s)", query[:120], len(hits), provider)
    return SearchOutcome(query, hits, failed=False)


def _duckduckgo(query: str, count: int) -> list[SearchHit]:
    results = DDGS(timeout=SEARCH_TIMEOUT_SECONDS).text(query, max_results=count)
    return [
        SearchHit(title=str(r.get("title", "")), url=str(r.get("href", "")), snippet=str(r.get("body", "")))
        for r in results
    ][:count]


# DataForSEO's wire vocabulary (theirs, never ours to rename): only
# ORGANIC results are evidence; ads, answer boxes, and packs are not
# pages a claim can ground to. Searches pin US English for now (a
# locale knob is a future product decision, not an accident).
_DATAFORSEO_ORGANIC = "organic"
_DATAFORSEO_TASK_OK = 20000
# "No Search Results": the provider's honest-empty status, success-shaped.
DATAFORSEO_NO_RESULTS = 40102
# "Internal SE Server Error": the provider's own upstream failed, a
# documented transient worth exactly ONE retry (measured at ~20% of
# searches during a degraded window; each miss pushes the model
# toward re-querying its budget away). Binary pause between tries.
DATAFORSEO_SE_ERROR = 40101
_DATAFORSEO_RETRY_PAUSE_SECONDS = 2
# Their billing floor: depths below 10 cost the same 10.
_DATAFORSEO_DEPTH_FLOOR = 10
_DATAFORSEO_LANGUAGE = "en"
_DATAFORSEO_LOCATION_US = 2840


def _dataforseo(query: str, count: int) -> list[SearchHit]:
    """One retry on the provider's OWN transient (40101), then the
    failure is real and the failed flag tells it."""
    try:
        return _dataforseo_once(query, count)
    except ValueError as e:
        if str(DATAFORSEO_SE_ERROR) not in str(e):
            raise
        time.sleep(_DATAFORSEO_RETRY_PAUSE_SECONDS)
        return _dataforseo_once(query, count)


def _dataforseo_once(query: str, count: int) -> list[SearchHit]:
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
    if response.status_code != 200:
        raise ValueError(f"dataforseo returned {response.status_code}")
    tasks = response.json().get("tasks") or []
    task = tasks[0] if tasks else {}
    if task.get("status_code") == DATAFORSEO_NO_RESULTS:
        # 40102 IS the answer, not an error: the query matched nothing.
        # Read as failure it made the model burn its tool budget
        # re-asking variants of a question with no answer.
        return []
    if task.get("status_code") != _DATAFORSEO_TASK_OK:
        # A 200 envelope can carry a failed TASK (insufficient balance
        # is the likely paid-door failure); reading it as an honest
        # zero-hit drought is exactly the misdiagnosis the failed flag
        # exists to prevent.
        raise ValueError(f"dataforseo task returned {task.get('status_code')}: {task.get('status_message')}")
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
    together so registering a provider forces both questions."""

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

"""The FREE provider and the DEFAULT: DuckDuckGo, keyless with zero
setup, so web search works out of the box and offloads the paid
provider.

The engine is DuckDuckGo's OWN, called through the ddgs library's
engine class rather than its aggregator. The aggregator fans a query
out to a dozen scrapers, drops every engine that refuses (it reads
any non-200 as "no results"), and answers from whichever is left, so
under partial throttling it silently swaps indexes and an
honest-looking empty can mean "Yahoo has not indexed it". The engine
class exposes the raw status, which is the one fact a rate limit
needs: html.duckduckgo.com answers a bot challenge as 202 (a page
with no results in it), and a plain refusal as 403, 429, or 503. A
200 with nothing in it is an honest empty.

`duckduckgo` below is this module's VendorRun (the contract, with
every vendor's shared rules, lives in providers/base.py)."""

from __future__ import annotations

from typing import NamedTuple

# PRIVATE ddgs paths, pinned tight in pyproject: a minor bump can move
# them, and an ImportError here takes the whole agents app down at
# import time, not just the free provider.
from ddgs.engines.duckduckgo import Duckduckgo
from ddgs.exceptions import DDGSException

from .base import AttemptThrottled, AttemptUnreachable, ProviderSpec, SearchHit
from .registry import register

_REFUSALS = frozenset({202, 403, 429, 503})
_REGION = "us-en"
_SAFESEARCH = "moderate"


class _Page(NamedTuple):
    """One results page as the engine answered it: the status the
    library would have thrown away, and the hits parsed off a 200
    (empty on any other status)."""

    status_code: int
    hits: list[SearchHit]


def _fetch(query: str) -> _Page:
    """The one library touch: the engine's own request path (its
    headers and TLS shape included) and its own parser, with the
    status kept. Tests script pages, not clients. The library wraps
    every transport failure in its own exception types (a timeout as
    TimeoutException, a refused or dropped connection as a bare
    DDGSException), which is why both read as UNREACHABLE here."""
    engine = Duckduckgo(timeout=SPEC.timeout_seconds)
    payload = engine.build_payload(query=query, region=_REGION, safesearch=_SAFESEARCH, timelimit=None)
    try:
        response = engine.http_client.request(engine.search_method, engine.search_url, data=payload)
    finally:
        engine.http_client.client.close()
    if response.status_code != 200:
        return _Page(response.status_code, [])
    results = engine.post_extract_results(engine.extract_results(response.text))
    return _Page(200, [SearchHit(title=r.title, url=r.href, snippet=r.body) for r in results])


def duckduckgo(query: str, count: int) -> list[SearchHit]:
    try:
        page = _fetch(query)
    except DDGSException as e:
        raise AttemptUnreachable(str(e)) from e
    if page.status_code in _REFUSALS or page.status_code >= 500:
        # The refusal set carries the codes that mean slow-down on
        # THIS wire (202 the bot challenge, 403 a plain refusal); the
        # 5xx leg is the contract's own rule (providers/base.py).
        raise AttemptThrottled(f"duckduckgo returned {page.status_code}")
    if page.status_code != 200:
        raise ValueError(f"duckduckgo returned {page.status_code}")
    return page.hits[:count]


SPEC = ProviderSpec(
    run=duckduckgo,
    # Keyless by design (no config schema); the public endpoint
    # needs nothing.
    metered=False,
    display="DuckDuckGo",
)

register(SPEC)

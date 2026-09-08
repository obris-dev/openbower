"""The METERED provider: Serper, real Google SERPs through
serper.dev on prepaid credits (a [serper] api_key in
config/tools.toml; 2,500 free trial queries, then from ~$0.30/1k),
with no operator surcharge: a site:-scoped x-ray costs the same one
credit as a plain search.

The wire constant below is Serper's vocabulary (theirs, never ours
to rename): organic results are the evidence rows, and a response
with no organic key IS the honest zero-hit answer. A 4xx is a
DECIDED error on this wire: their 403 is Unauthorized and a 400 can
be a drained balance, and both need an operator, never a retry.

`serper` below is this module's VendorRun (the contract, with every
vendor's shared rules, lives in providers/base.py)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import AttemptThrottled, ProviderSpec, SearchHit, parse_retry_after
from .registry import register, vendor_table

# Their result envelope: only ORGANIC rows are evidence, and the
# key's absence IS the honest zero.
_ORGANIC = "organic"
# Their credit step: up to 10 results is one credit, more is two.
_ONE_CREDIT_DEPTH = 10


@dataclass(frozen=True)
class Config:
    """The [serper] table's schema: field names ARE the operator's
    keys (the spec derives config_keys from them), and run reads a
    constructed instance, so each fact exists once, typed."""

    api_key: str


def _config() -> Config:
    return Config(**vendor_table(serper.__name__))


def serper(query: str, count: int) -> list[SearchHit]:
    response = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": _config().api_key, "Content-Type": "application/json"},
        # Searches pin US English for now, re-made from the retired
        # vendor deliberately (a locale knob is a future product
        # decision, not an accident): the locale decides which
        # regional profile subdomains an x-ray answers with, so it
        # must not float on a vendor default.
        json={"q": query, "num": min(count, _ONE_CREDIT_DEPTH), "gl": "us", "hl": "en"},
        timeout=SPEC.timeout_seconds,
    )
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        raise AttemptThrottled(
            "serper returned 429", retry_after=parse_retry_after(response.headers.get("Retry-After", ""))
        )
    if response.status_code >= 500:
        raise AttemptThrottled(f"serper returned {response.status_code}")
    if response.status_code != 200:
        # The body's own message is the only place the cause can land
        # (their errors are JSON with a message field; a drained
        # balance says so): the operator log must name it, since the
        # user-facing copy is deliberately generic.
        try:
            detail = str(response.json().get("message", ""))[:200]
        except ValueError:
            detail = ""
        raise ValueError(f"serper returned {response.status_code}" + (f": {detail}" if detail else ""))
    organic = response.json().get(_ORGANIC) or []
    # No shape hedging on the rows: a malformed entry raises and
    # lands as ONE diagnosed error, the honest read of a broken
    # payload. Nulls guard on every field, since a null title would
    # otherwise render as the string "None" in evidence the model
    # reads and cites.
    return [
        SearchHit(
            title=str(hit.get("title") or ""),
            url=str(hit.get("link") or ""),
            snippet=str(hit.get("snippet") or ""),
        )
        for hit in organic
    ][:count]


SPEC = ProviderSpec(
    run=serper,
    config_schema=Config,
    metered=True,
    display="Serper",
)

register(SPEC)

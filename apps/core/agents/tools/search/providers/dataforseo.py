"""The METERED provider: DataForSEO, pay-as-you-go Google SERPs on
a non-expiring balance ($1 trial credit; a [dataforseo] login and
password in config/tools.toml; the live endpoint at ~$2/1k).

The constants below are DataForSEO's wire vocabulary (theirs, never
ours to rename): only ORGANIC results are evidence; ads, answer
boxes, and packs are not pages a claim can ground to. Searches pin US
English for now (a locale knob is a future product decision, not an
accident)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import AttemptThrottled, ProviderSpec, SearchHit, parse_retry_after
from .registry import register, vendor_table


@dataclass(frozen=True)
class Config:
    """The [dataforseo] table's schema: field names ARE the operator's
    keys (the spec derives config_keys from them), and run reads a
    constructed instance, so each fact exists once, typed."""

    login: str
    password: str


def _config() -> Config:
    return Config(**vendor_table(dataforseo.__name__))


_ORGANIC = "organic"
_TASK_OK = 20000
# "No Search Results": the provider's honest-empty status, success-shaped.
NO_RESULTS = 40102
# "Internal SE Server Error": the provider's own upstream failed, a
# documented transient (measured at ~20% of searches during a degraded
# window) that asks for exactly what a rate limit asks for: the same
# query again, a little later.
SE_ERROR = 40101
# Their billing floor: depths below 10 cost the same 10.
_DEPTH_FLOOR = 10
_LANGUAGE = "en"
_LOCATION_US = 2840


def dataforseo(query: str, count: int) -> list[SearchHit]:
    keys = _config()
    # Their billing multiplies the charge by 5 when the keyword carries
    # a search operator (site:, intitle:, inurl:, ...), on regular and
    # advanced alike. Contact search injects site: on every call, so
    # every contact query bills at the operator rate; an operator in a
    # model-authored web search does the same.
    response = httpx.post(
        "https://api.dataforseo.com/v3/serp/google/organic/live/regular",
        json=[
            {
                "keyword": query,
                "language_code": _LANGUAGE,
                "location_code": _LOCATION_US,
                "depth": max(count, _DEPTH_FLOOR),
            }
        ],
        auth=(keys.login, keys.password),
        timeout=SPEC.timeout_seconds,
    )
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        raise AttemptThrottled(
            "dataforseo returned 429", retry_after=parse_retry_after(response.headers.get("Retry-After", ""))
        )
    if response.status_code != 200:
        raise ValueError(f"dataforseo returned {response.status_code}")
    tasks = response.json().get("tasks") or []
    task = tasks[0] if tasks else {}
    status = task.get("status_code")
    if status == NO_RESULTS:
        # 40102 IS the answer, not an error: the query matched nothing.
        # Read as failure it made the model burn its tool budget
        # re-asking variants of a question with no answer.
        return []
    if status == SE_ERROR:
        raise AttemptThrottled(f"dataforseo task returned {status}: {task.get('status_message')}")
    if status != _TASK_OK:
        # A 200 envelope can carry a failed TASK (insufficient balance
        # is the likely paid-provider failure); reading it as an honest
        # zero-hit drought is exactly the misdiagnosis the status
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
        if r.get("type") == _ORGANIC
    ][:count]


SPEC = ProviderSpec(
    run=dataforseo,
    config_schema=Config,
    metered=True,
    # Their live endpoint computes per request (10-20s routinely,
    # spikes beyond), so the general quick-or-dead default is wrong
    # for it.
    timeout_seconds=64,
    display="DataForSEO",
)

register(SPEC)

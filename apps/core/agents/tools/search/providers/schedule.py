"""The retry SCHEDULE: `search(query, provider=...) -> ProviderAnswer`
drives the registered provider through the family's bounded backoff
and DECIDES the outcome, converting the provider's attempt signals
into the family's typed errors the moment the schedule gives up,
carrying the audit facts (which provider, how many tries). This is
the search family's OWN driver, not a contract every family needs: a
family whose backend has no refusal worth retrying builds none of it.

The schedule knows nothing about which tool asked; the runtime
records that. Retrying is the schedule's, never the model's: a rate
limit is retried here, SAME query, and rephrasing stays the model's
decision. Each provider classifies its own refusals into the attempt
signals (providers speak differently); the schedule owns what a
refusal MEANS."""

from __future__ import annotations

import logging
import time

import httpx

from ....constants import SEARCH_BACKOFF_SECONDS, SEARCH_HIT_COUNT, SearchStatus
from ..errors import SearchErrored, SearchNotConfigured, SearchRateLimited, SearchUnreachable
from .base import AttemptThrottled, AttemptUnreachable, ProviderAnswer
from .registry import get, provider_status

logger = logging.getLogger(__name__)

# Sleeping is a module seam so tests assert the schedule instead of
# waiting it out.
_sleep = time.sleep


def search(query: str, *, provider: str) -> ProviderAnswer:
    """One query's answer from its provider (REQUIRED: the caller says
    which; the tool's spec is the one place that decision lives). A
    rate limit is retried, SAME query, once per step of
    SEARCH_BACKOFF_SECONDS; unreachable and error are per-query
    hazards, reported once and not retried. An unconfigured or
    unregistered provider raises SearchNotConfigured without a call:
    the runtime's gates keep it from being asked, so arriving here is
    worth a log line."""
    if provider_status(provider) is not SearchStatus.OPEN:
        logger.warning("search asked of an unconfigured provider (%r)", provider)
        raise SearchNotConfigured(provider=provider, attempts=0)
    entry = get(provider)
    # The schedule is the waits BETWEEN tries, so there is one more
    # try than there are waits: the first try, then one retry after
    # each wait. A rate limit on the last try has no wait left and is
    # the answer.
    last_try = len(SEARCH_BACKOFF_SECONDS) + 1
    for attempt in range(1, last_try + 1):
        try:
            hits = entry.run(query, SEARCH_HIT_COUNT)
        except AttemptThrottled as e:
            if attempt == last_try:
                logger.warning("search rate limited after %d tries (%s): %s", attempt, provider, e)
                raise SearchRateLimited(provider=provider, attempts=attempt) from e
            wait = _wait_before_retry(attempt, retry_after=e.retry_after)
            logger.info("search rate limited (%s); retrying the same query in %ss", provider, wait)
            _sleep(wait)
            continue
        except (AttemptUnreachable, httpx.TransportError) as e:
            logger.warning("search provider unreachable (%s: %s): %s", provider, type(e).__name__, e)
            raise SearchUnreachable(provider=provider, attempts=attempt) from e
        except Exception as e:
            # Every other failure is one error on purpose: a bad
            # payload, a drained balance, and an unexpected status all
            # mean the provider answered wrongly for THIS query, and
            # none of them asks for a retry.
            logger.warning("search failed (%s: %s): %s", provider, type(e).__name__, e)
            raise SearchErrored(provider=provider, attempts=attempt) from e
        logger.info("search %r -> %d hits (%s, %d tries)", query[:120], len(hits), provider, attempt)
        return ProviderAnswer(hits, provider, attempts=attempt)
    raise AssertionError("unreachable: the last try returns")


def _wait_before_retry(attempt: int, *, retry_after: float | None) -> float:
    """The wait after try `attempt` (1-based): the schedule's step for
    it, unless the provider asked for a specific delay, which wins,
    clamped to the schedule's longest step so a provider cannot park a
    row for as long as it likes."""
    scheduled = SEARCH_BACKOFF_SECONDS[attempt - 1]
    if retry_after is None:
        return scheduled
    return min(retry_after, max(SEARCH_BACKOFF_SECONDS))

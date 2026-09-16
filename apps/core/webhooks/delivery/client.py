"""The one HTTP client deliveries share. Built on first use and kept
for the process (the encryption field's `_fernet()` idiom): an
httpx.Client owns an SSL context and a connection pool, both expensive
to make and worth keeping across the many POSTs a flush pass makes to
the same few hosts. The client is thread-safe, so a gunicorn worker and
the cron process each hold one. Timeouts are per request (the sender
passes them), so a settings override reaches the next send without a
rebuild."""

from __future__ import annotations

from functools import lru_cache

import httpx

from ..constants import WEBHOOK_POOL_CONNECTIONS, WEBHOOK_POOL_KEEPALIVE


def _transport() -> httpx.BaseTransport | None:
    """The seam tests patch with an httpx.MockTransport (after a reset,
    so the next build picks it up); None means httpx's own transport."""
    return None


@lru_cache(maxsize=1)
def shared_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=False,
        limits=httpx.Limits(max_connections=WEBHOOK_POOL_CONNECTIONS, max_keepalive_connections=WEBHOOK_POOL_KEEPALIVE),
        transport=_transport(),
    )


def reset_shared_client() -> None:
    """Close and forget the client, so the next send builds a fresh one
    (tests swapping the transport; a process reconfiguring itself)."""
    if shared_client.cache_info().currsize:
        shared_client().close()
    shared_client.cache_clear()

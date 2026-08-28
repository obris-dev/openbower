"""Every data-service URL this client calls.

One module owns the upstream URL shapes, so a path change on the data
service is a one-place edit here. Lives INSIDE the client package because
transport is its only consumer (auth_client's idp_urls sits at that app's
root instead because views consume it too, for the logout navigation).
Every call here is server-to-server, so the base is
OPENBOWER_DATA_INTERNAL_URL, the TRANSPORT (a containerized app's network
path can differ from the canonical origin); OPENBOWER_DATA_URL stays the
data service's identity, which is what the token names as its audience.
The version prefix is the DATA SERVICE'S own
(OPENBOWER_DATA_API_VERSION), decoupled from this app's
API_VERSION_PREFIX.
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings


def _internal_base() -> str:
    """Transport for the calls this process makes, never the identity.

    Named to match auth_client.idp_urls, where _base() is the canonical
    origin and _internal_base() the transport: one vocabulary across both
    upstream-URL modules, so a reader cannot pick the wrong one here."""
    return f"{settings.OPENBOWER_DATA_INTERNAL_URL}/{settings.OPENBOWER_DATA_API_VERSION}"


def lookalikes_url() -> str:
    """POST: the look-alike query (list_id or inline domains)."""
    return f"{_internal_base()}/lookalikes"


def _run_segment(run_id: str) -> str:
    """The caller-supplied id, percent-encoded: Django DECODES %XX before
    routing, so <str:id> happily captures ? & # and friends; interpolated
    raw they would inject query params into the token-bearing upstream
    request. Encoding the segment makes the id inert whatever it holds."""
    return quote(run_id, safe="")


def lookalike_run_url(run_id: str, *, limit: int | None = None) -> str:
    """GET: the async run-status poll (the 202 lifecycle). `limit` sizes
    the completed page so warm and cold cohorts answer alike."""
    suffix = f"?limit={limit}" if limit is not None else ""
    return f"{_internal_base()}/lookalikes/runs/{_run_segment(run_id)}{suffix}"


def lookalike_run_cancel_url(run_id: str) -> str:
    """POST: stop a pending/running run (terminal runs no-op)."""
    return f"{_internal_base()}/lookalikes/runs/{_run_segment(run_id)}/cancel"

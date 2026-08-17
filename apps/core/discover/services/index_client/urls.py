"""Every data-service URL this client calls.

One module owns the upstream URL shapes, so a path change on the data
service is a one-place edit here. Lives INSIDE the client package because
transport is its only consumer (auth_client's idp_urls sits at that app's
root instead because views consume it too, for the logout navigation).
The base comes from OPENBOWER_DATA_URL; the version prefix is the DATA
SERVICE'S own (OPENBOWER_DATA_API_VERSION), decoupled from this app's
API_VERSION_PREFIX.
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings


def _base() -> str:
    return f"{settings.OPENBOWER_DATA_URL}/{settings.OPENBOWER_DATA_API_VERSION}"


def lookalikes_url() -> str:
    """POST: the look-alike query (list_id or inline domains)."""
    return f"{_base()}/lookalikes"


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
    return f"{_base()}/lookalikes/runs/{_run_segment(run_id)}{suffix}"


def lookalike_run_cancel_url(run_id: str) -> str:
    """POST: stop a pending/running run (terminal runs no-op)."""
    return f"{_base()}/lookalikes/runs/{_run_segment(run_id)}/cancel"

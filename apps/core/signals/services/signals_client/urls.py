"""Every signals-service URL this client calls.

One module owns the upstream URL shapes, the same way
discover.services.index_client.urls does for the data service. Every
call is server-to-server, so the base is OPENBOWER_SIGNALS_INTERNAL_URL
(the transport); OPENBOWER_SIGNALS_URL stays the service's identity, the
audience the token names. The version prefix is the service's own
(OPENBOWER_SIGNALS_API_VERSION).
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from django.conf import settings


def _internal_base() -> str:
    """Transport for the calls this process makes, never the identity."""
    return f"{settings.OPENBOWER_SIGNALS_INTERNAL_URL}/{settings.OPENBOWER_SIGNALS_API_VERSION}"


def _segment(value: str) -> str:
    """A caller-supplied path segment, percent-encoded: interpolated raw, an
    id holding ? & # would inject query params into the token-bearing
    upstream request (Django decodes %XX before routing, so a route's
    <str:id> can capture them)."""
    return quote(value, safe="")


def _query(**params: str | int | None) -> str:
    """The query string of the params given (None: left out)."""
    given = {name: value for name, value in params.items() if value is not None}
    if not given:
        return ""
    return f"?{urlencode(given)}"


def catalog_url() -> str:
    """GET: every signal, and the limits a setup is held to."""
    return f"{_internal_base()}/signals"


def signal_events_url(signal: str, *, after: str | None = None, limit: int | None = None) -> str:
    """GET: a signal's events, newest first, to choose a preview's from."""
    return f"{_internal_base()}/signals/{_segment(signal)}/events{_query(after=after, limit=limit)}"


def listeners_url(*, after: str | None = None, limit: int | None = None) -> str:
    """GET: the account's listeners, newest first. POST: create one."""
    return f"{_internal_base()}/listeners{_query(after=after, limit=limit)}"


def listener_url(listener_id: str) -> str:
    """GET, PATCH (a new label, a new setup or both), DELETE."""
    return f"{_internal_base()}/listeners/{_segment(listener_id)}"


def listener_versions_url(listener_id: str, *, after: str | None = None, limit: int | None = None) -> str:
    """GET: a listener's saved setups, newest first."""
    return f"{listener_url(listener_id)}/versions{_query(after=after, limit=limit)}"


def listener_matches_url(listener_id: str, *, after: str | None = None, limit: int | None = None) -> str:
    """GET: a listener's matches, oldest first, after a cursor from a
    previous page."""
    return f"{listener_url(listener_id)}/matches{_query(after=after, limit=limit)}"


def previews_url() -> str:
    """POST: try a setup on chosen events."""
    return f"{_internal_base()}/previews"


def preview_url(preview_id: str) -> str:
    """GET: the preview and its rows so far. DELETE: cancel it."""
    return f"{_internal_base()}/previews/{_segment(preview_id)}"

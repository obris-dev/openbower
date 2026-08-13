"""The token-rotation state machine: everything that spends, defers, or
retires the session's cached IdP token pair. Callers are the service
module's public paths; each function here carries its WHY once.

The public entry points take their own row locks so at most one request
spends the single-use refresh token; the two entry points differ only in
how they find the row and how they detect that a peer already rotated
(freshness for routine expiry, token identity when a downstream server
rejected a specific token).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from auth_client.models import AppSession

from ..oauth import AuthUpstreamError, AuthUpstreamUnavailable, OAuthClientService

logger = logging.getLogger("auth_client")


def refresh_skew() -> timedelta:
    """Early-refresh buffer: treat the access token as due for rotation this
    many seconds BEFORE its actual expiry. A token handed to a downstream
    resource server (data) is introspected there a beat later; without a
    buffer a token with a sliver of life left passes the local check, then
    lapses in-flight and the downstream call fails. The buffer must stay well
    under the access-token lifetime and below AUTH_REFRESH_COOLDOWN_SECONDS
    (so the transient-failure cooldown below still parks a request on the fast
    path)."""
    return timedelta(seconds=settings.AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS)


def fresh_enough(session: AppSession) -> bool:
    """True while the cached access token has more than the skew buffer of
    life left, i.e. it is safe to hand to a downstream call without rotating."""
    return session.access_expires_at > timezone.now() + refresh_skew()


def _spend_refresh_token(session: AppSession) -> AppSession:
    """Spend the (single-use) refresh token and store the new pair.
    Raises the upstream errors for the caller to map to an outcome."""
    tokens = OAuthClientService.Global.refresh(refresh_token=session.refresh_token)
    session.access_token = tokens.access_token
    session.refresh_token = tokens.refresh_token
    session.access_expires_at = timezone.now() + timedelta(seconds=tokens.expires_in)
    session.save(update_fields=["access_token", "refresh_token", "access_expires_at", "updated_at"])
    return session


def _back_off(session: AppSession) -> AppSession:
    """The IdP blinked mid-refresh: keep the session on its cached token
    and push the expiry to now + skew + cooldown, so the NEXT requests
    take the fast path instead of re-attempting (and re-timing-out) every
    request and thundering-herding a recovering IdP. Past the skew too,
    else the fast-path check (which subtracts the skew) fires again
    immediately. Stays well under the access-token lifetime."""
    cooldown = timedelta(seconds=settings.AUTH_REFRESH_COOLDOWN_SECONDS)
    session.access_expires_at = timezone.now() + refresh_skew() + cooldown
    session.save(update_fields=["access_expires_at", "updated_at"])
    return session


def _revoke_now(session: AppSession) -> None:
    """Tombstone the session (a terminal upstream rejection: the refresh
    token is dead, only a fresh login recovers)."""
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at", "updated_at"])


def rotate_or_settle(session: AppSession, *, transient_propagates: bool) -> AppSession | None:
    """Rotate inside the caller's lock and map the three outcomes: rotated
    (return the fresh row), terminal rejection (revoke, return None), or a
    transient IdP failure (back off, then EITHER serve the stale token,
    for the routine expiry path where a brief outage must not log anyone
    out, OR propagate, for the downstream-rejected path whose caller maps
    it to 503 rather than re-login).

    The Unavailable clause MUST precede the terminal one: it subclasses
    AuthUpstreamError, and matching the superclass first would revoke live
    sessions over a blip."""
    try:
        return _spend_refresh_token(session)
    except AuthUpstreamUnavailable:
        _back_off(session)
        if transient_propagates:
            raise
        logger.warning("IdP unreachable during refresh; keeping session %s with stale token", session.id)
        return session
    except AuthUpstreamError:
        _revoke_now(session)
        return None


def refresh_locked(token_hash: str) -> AppSession | None:
    """Routine expiry path: rotate under a row lock found by TOKEN HASH;
    peers block, re-check freshness, and see the refreshed row. The lock
    spans the refresh HTTP call (bounded by AUTH_HTTP_TIMEOUT_SECONDS);
    contention is one user's own tabs. Transient failures serve the stale
    token (a brief IdP outage must not log anyone out)."""
    with transaction.atomic():
        try:
            session = AppSession.objects.select_for_update().get(token_hash=token_hash, revoked_at__isnull=True)
        except AppSession.DoesNotExist:
            return None
        if fresh_enough(session):
            # A peer refreshed while we waited on the lock.
            return session
        return rotate_or_settle(session, transient_propagates=False)


def force_refresh(session: AppSession, *, stale_token: str) -> AppSession | None:
    """Downstream-rejection path: a resource server's introspection said
    the token is inactive, so expiry cannot be the signal. Single-flight
    by token IDENTITY: if the locked row's token differs from the one that
    was rejected, a peer already rotated, so return it without spending
    another refresh. Transient failures back off then PROPAGATE (the
    caller maps them to 503, never re-login); terminal ones revoke."""
    with transaction.atomic():
        try:
            session = AppSession.objects.select_for_update().get(pk=session.pk, revoked_at__isnull=True)
        except AppSession.DoesNotExist:
            return None
        if session.access_token != stale_token:
            # A peer already rotated; hand back the fresh token instead
            # of burning another refresh.
            return session
        return rotate_or_settle(session, transient_propagates=True)

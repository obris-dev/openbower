"""App session lifecycle: the server-side record behind the cookie.

`create` mints the opaque cookie token (returned ONCE, stored only as a
SHA-256 hash); `resolve` is the per-request path, silently rotating the
IdP token pair when the access token has expired; `revoke` ends a session.

System-level operations live under `AppSessionGlobal` (exposed as
`AppSessionService.Global`), matching the accounts services' Global-vs-
scoped split; the session credential is user-scoped, not account-scoped,
so there is no account-scoped instance side yet.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from auth_client.downstream import DownstreamTokenRejected
from auth_client.models import AppSession
from common.hashing import hash_token
from openbower_schema import AuthUser

from .oauth import AuthUpstreamError, AuthUpstreamUnavailable, OAuthClientService, TokenResponse

logger = logging.getLogger("auth_client")


def _refresh_skew() -> timedelta:
    """Early-refresh buffer: treat the access token as due for rotation this
    many seconds BEFORE its actual expiry. A token handed to a downstream
    resource server (data) is introspected there a beat later; without a
    buffer a token with a sliver of life left passes the local check, then
    lapses in-flight and the downstream call fails. The buffer must stay well
    under the access-token lifetime and below AUTH_REFRESH_COOLDOWN_SECONDS
    (so the transient-failure cooldown below still parks a request on the fast
    path)."""
    return timedelta(seconds=settings.AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS)


def _fresh_enough(session: AppSession) -> bool:
    """True while the cached access token has more than the skew buffer of
    life left, i.e. it is safe to hand to a downstream call without rotating."""
    return session.access_expires_at > timezone.now() + _refresh_skew()


class AppSessionGlobal:
    @staticmethod
    def create(*, user: AuthUser, tokens: TokenResponse) -> tuple[AppSession, str]:
        """Persist a session for the IdP identity + token pair.

        Returns `(record, raw_cookie_token)`; the raw token exists only
        in this return value and the cookie.
        """
        raw = secrets.token_urlsafe(32)
        record = AppSession.objects.create(
            token_hash=hash_token(raw),
            user_id=user.id,
            account_id=user.account_id,
            email=user.email,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            access_expires_at=timezone.now() + timedelta(seconds=tokens.expires_in),
        )
        return record, raw

    @staticmethod
    def get_live(raw: str) -> AppSession | None:
        """Cookie token -> live (non-revoked) session, or None. Pure
        lookup, never refreshes. Use when you must NOT trigger a token
        rotation (logout) or when staleness doesn't matter."""
        try:
            return AppSession.objects.get(token_hash=hash_token(raw), revoked_at__isnull=True)
        except AppSession.DoesNotExist:
            return None

    @staticmethod
    def resolve(raw: str) -> AppSession | None:
        """Cookie token -> live session, or None.

        When the cached access token has expired, rotate the pair at the
        IdP inline (this is what keeps entitlement checks live without
        re-login). Fast path (token still fresh) takes no lock; the
        expired path serializes via `_refresh_locked` so concurrent tabs
        are correct on their own, not by leaning on any IdP grace window.
        """
        session = AppSessionGlobal.get_live(raw)
        if session is None:
            return None
        if _fresh_enough(session):
            return session
        return AppSessionGlobal._refresh_locked(hash_token(raw))

    @staticmethod
    def _refresh_locked(token_hash: str) -> AppSession | None:
        """Refresh the IdP token pair under a row lock so at most one
        request rotates it. Concurrent requests block on the lock, then
        re-check and see the already-refreshed token instead of each
        spending the (single-use, rotating) refresh token.

        Terminal `AuthUpstreamError` (a 4xx rejection, dead refresh
        token) revokes the session. A transient `AuthUpstreamUnavailable`
        (IdP down / 5xx / timeout) leaves the session intact and returns
        it with the still-cached token: a brief IdP outage must not log
        everyone out. The row lock is held across the refresh HTTP call,
        bounded by AUTH_HTTP_TIMEOUT_SECONDS; contention is one user's
        own tabs, so this is acceptable.
        """
        with transaction.atomic():
            try:
                session = AppSession.objects.select_for_update().get(token_hash=token_hash, revoked_at__isnull=True)
            except AppSession.DoesNotExist:
                return None
            # A concurrent request may have refreshed while we waited.
            if _fresh_enough(session):
                return session
            try:
                tokens = OAuthClientService.Global.refresh(refresh_token=session.refresh_token)
            except AuthUpstreamUnavailable:
                # Transient IdP failure: keep the session, but push the expiry
                # out by a short cooldown so the NEXT request takes the fast
                # path instead of re-attempting (and re-timing-out) the refresh
                # every request and thundering-herding a recovering IdP. Set it
                # past the skew buffer too (now + skew + cooldown), else the
                # fast-path check (which subtracts the skew) would fire again
                # immediately and defeat the cooldown. Stays well under the
                # access-token lifetime.
                logger.warning("IdP unreachable during refresh; keeping session %s with stale token", session.id)
                cooldown = timedelta(seconds=settings.AUTH_REFRESH_COOLDOWN_SECONDS)
                session.access_expires_at = timezone.now() + _refresh_skew() + cooldown
                session.save(update_fields=["access_expires_at", "updated_at"])
                return session
            except AuthUpstreamError:
                session.revoked_at = timezone.now()
                session.save(update_fields=["revoked_at", "updated_at"])
                return None
            session.access_token = tokens.access_token
            session.refresh_token = tokens.refresh_token
            session.access_expires_at = timezone.now() + timedelta(seconds=tokens.expires_in)
            session.save(update_fields=["access_token", "refresh_token", "access_expires_at", "updated_at"])
            return session

    @staticmethod
    def force_refresh(session: AppSession, *, stale_token: str) -> AppSession | None:
        """Rotate the token pair NOW, even though the cached expiry may not
        have passed, because a downstream resource server rejected the token
        (its introspection said inactive/expired/revoked). Causes: the token
        was revoked upstream, or it lapsed in the sliver between resolve() and
        the downstream call.

        Single-flight by token IDENTITY (not expiry): if a peer already
        rotated while we waited on the lock, the row's token differs from the
        one we tried, so return it without spending another single-use refresh
        token. A terminal failure (dead refresh token) revokes the session and
        returns None (the caller re-logs-in). A transient IdP failure
        propagates as AuthUpstreamUnavailable, since serving the same rejected
        token again is pointless: the caller treats it as upstream-unavailable,
        NOT a credential problem, so it does not force re-login.
        """
        with transaction.atomic():
            try:
                session = AppSession.objects.select_for_update().get(pk=session.pk, revoked_at__isnull=True)
            except AppSession.DoesNotExist:
                return None
            if session.access_token != stale_token:
                # A concurrent request already rotated; hand back the fresh
                # token instead of burning another refresh.
                return session
            try:
                tokens = OAuthClientService.Global.refresh(refresh_token=session.refresh_token)
            except AuthUpstreamUnavailable:
                # Transient IdP failure. AuthUpstreamUnavailable subclasses
                # AuthUpstreamError, so this clause MUST precede the terminal
                # one, else a momentary IdP blip would wrongly revoke a valid
                # session. Push the expiry past the skew + cooldown (as
                # _refresh_locked does) so the next resolve() does not immediately
                # re-attempt during the outage, then propagate: the caller maps
                # it to 503 (upstream down), NOT re-login.
                cooldown = timedelta(seconds=settings.AUTH_REFRESH_COOLDOWN_SECONDS)
                session.access_expires_at = timezone.now() + _refresh_skew() + cooldown
                session.save(update_fields=["access_expires_at", "updated_at"])
                raise
            except AuthUpstreamError:
                # Terminal rejection (dead refresh token): revoke and re-login.
                session.revoked_at = timezone.now()
                session.save(update_fields=["revoked_at", "updated_at"])
                return None
            session.access_token = tokens.access_token
            session.refresh_token = tokens.refresh_token
            session.access_expires_at = timezone.now() + timedelta(seconds=tokens.expires_in)
            session.save(update_fields=["access_token", "refresh_token", "access_expires_at", "updated_at"])
            return session

    @staticmethod
    def call_with_refresh(session: AppSession, call):
        """Run `call(access_token)` against a downstream resource server,
        transparently refreshing the token once if the server rejects it.

        The single place the app's on-behalf-of-a-user downstream calls live,
        so no view re-implements token-refresh-retry. `call` receives the
        access token and must raise `DownstreamTokenRejected` on an upstream
        401 (every resource client raises the same signal). Flow: call with
        the current token; on rejection force a refresh and call ONCE more with
        the new token. Re-raises `DownstreamTokenRejected` only when the
        session is dead or a brand-new token is STILL rejected (the caller maps
        that to re-login); a transient refresh failure surfaces as
        `AuthUpstreamUnavailable` (upstream down, not a credential problem).
        Any other exception `call` raises (a scope 403, an upstream 5xx)
        passes straight through.
        """
        try:
            return call(session.access_token)
        except DownstreamTokenRejected:
            refreshed = AppSessionGlobal.force_refresh(session, stale_token=session.access_token)
            if refreshed is None:
                raise  # session is dead -> caller re-logs-in
            # Retry once with the fresh token; a second rejection propagates.
            # NOTE: the caller's `session` (e.g. request.user.session) still
            # holds the pre-refresh token in memory; anything reading it AFTER
            # this call must use `refreshed`. Harmless for the current single
            # call-then-serialize view; revisit when a base-view mixin lands.
            return call(refreshed.access_token)

    @staticmethod
    def prune_revoked(*, older_than_days: int) -> int:
        """Delete sessions revoked more than `older_than_days` ago; return
        the count. Live (non-revoked) rows are never touched, their lifetime
        is governed by the IdP's rotating refresh token. For the scheduled
        prune command, so ORM access stays inside the service layer."""
        cutoff = timezone.now() - timedelta(days=older_than_days)
        deleted, _ = AppSession.objects.filter(revoked_at__isnull=False, revoked_at__lt=cutoff).delete()
        return deleted

    @staticmethod
    def revoke(session: AppSession) -> None:
        """End the session locally + best-effort revoke upstream."""
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at", "updated_at"])
        # Revoke the REFRESH token: the toolkit cascades that to its
        # access token, so one call kills the whole pair. Revoking only
        # the access token would leave the refresh token live upstream
        # until natural expiry.
        OAuthClientService.Global.revoke(token=session.refresh_token, token_type_hint="refresh_token")


class AppSessionService:
    """Session lifecycle service. All operations are system-level (the
    credential is user-scoped, not account-scoped), so they live under
    `Global`, mirroring the accounts services."""

    Global = AppSessionGlobal

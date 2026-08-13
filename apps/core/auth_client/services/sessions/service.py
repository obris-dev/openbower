"""App session lifecycle: the server-side record behind the cookie.

`create` mints the opaque cookie token (returned ONCE, stored only as a
SHA-256 hash); `resolve` is the per-request path, delegating expired
tokens to the rotation module; `revoke` ends a session. Token mechanics
live in rotation.py; this module owns the cookie <-> session lifecycle.

System-level operations live under `AppSessionGlobal` (exposed as
`AppSessionService.Global`), matching the accounts services' Global-vs-
scoped split; the session credential is user-scoped, not account-scoped,
so there is no account-scoped instance side yet.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from auth_client.models import AppSession
from openbower_kernel.hashing import hash_token
from openbower_schema import AuthUser

from ..oauth import OAuthClientService, TokenResponse
from . import rotation


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
        expired path serializes in the rotation module so concurrent tabs
        are correct on their own, not by leaning on any IdP grace window.
        """
        session = AppSessionGlobal.get_live(raw)
        if session is None:
            return None
        if rotation.fresh_enough(session):
            return session
        return rotation.refresh_locked(hash_token(raw))

    # Downstream-facing entry point (the resource clients' auth flows
    # rotate through it); mechanics in rotation.py.
    force_refresh = staticmethod(rotation.force_refresh)

    @staticmethod
    def prune_revoked(*, older_than_days: int) -> int:
        """Delete sessions revoked more than `older_than_days` ago; return
        the count. For the scheduled prune command, so ORM access stays
        inside the service layer."""
        cutoff = timezone.now() - timedelta(days=older_than_days)
        deleted, _ = AppSession.objects.filter(revoked_at__isnull=False, revoked_at__lt=cutoff).delete()
        return deleted

    @staticmethod
    def prune_unreachable() -> int:
        """Delete LIVE sessions no browser can present anymore; return the
        count. The cookie's max-age is fixed at login (it is never
        re-issued), so once that long has passed since creation the token
        cannot arrive on any request and the row only warehouses an
        encrypted refresh-token pair. Rows are deleted without upstream
        revocation: the IdP's rotating refresh token governs the pair's
        upstream lifetime, and an unreachable session cannot be used to
        rotate it anyway."""
        cutoff = timezone.now() - timedelta(seconds=settings.AUTH_COOKIE_MAX_AGE_SECONDS)
        deleted, _ = AppSession.objects.filter(revoked_at__isnull=True, created_at__lt=cutoff).delete()
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
    Global = AppSessionGlobal

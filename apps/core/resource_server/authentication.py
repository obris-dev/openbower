"""Bearer-token authentication for core as a RESOURCE SERVER.

Core never mints identity, and it holds NO secret: it is distributed
software users run per box, so it cannot be a confidential client. It
VERIFIES a machine token the hub minted by relaying the token to the
hub's /v1/auth/tokeninfo endpoint (self-introspection: the presented
token is the only credential). This is the machine lane (webhooks,
CLI/MCP); the browser lane stays on the session cookie
(AppSessionAuthentication). Both build the same AppUser and compose in a
per-view authentication_classes list; this class is attached only where
machines act, never globally (discover reads request.auth as a session,
and a machine token there is refused by the audience check).

Verified results cache (default cache, keyed by token fingerprint) for
TOKENINFO_CACHE_SECONDS bounded by the token's expiry, so a burst costs
one upstream round-trip; negative results cache briefly so a dead token
cannot make core a verify amplifier, and a per-IP miss-budget guards the
pre-auth flood the DRF throttles run too late to stop. Core gates on
AUDIENCE, not a scope: an authenticated app user is by definition allowed
to use core (it is the app's backend), so unlike the data leaf there is
no scope predicate; the audience check is what keeps a token minted for
some OTHER service (a session token, whose audience is the hub) from
being replayed on this lane.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, AuthenticationFailed, PermissionDenied, Throttled
from rest_framework.request import Request

from auth_client.authentication import AppUser
from openbower_kernel.hashing import hash_token

from .idp import verify_token
from .idp.transport import AuthUpstreamUnavailable

logger = logging.getLogger("resource_server")

_CACHE_PREFIX = "tokeninfo:"
_MISS_PREFIX = "tokeninfo-miss:"
_MISS_WINDOW_SECONDS = 60
_NEGATIVE_TTL_SECONDS = 30


class AuthServiceUnavailable(APIException):
    status_code = 503
    default_detail = "identity provider unavailable"
    default_code = "auth_unavailable"


class MachineTokenAuthentication(BaseAuthentication):
    """DRF authentication: Bearer token -> hub tokeninfo relay -> AppUser."""

    keyword = "Bearer"

    def authenticate(self, request: Request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None  # not ours; fall through (cookie may claim it, else 401)
        if len(header) != 2:
            raise AuthenticationFailed("invalid Authorization header")
        try:
            token = header[1].decode()
        except UnicodeError as e:
            raise AuthenticationFailed("invalid token encoding") from e

        try:
            claims = self._verify_cached(token, request)
        except AuthUpstreamUnavailable as e:
            # The token may be perfectly valid; a 503 tells a machine to
            # retry rather than discard a live credential on a hub blip.
            raise AuthServiceUnavailable() from e
        if not claims.get("active"):
            raise AuthenticationFailed("invalid or expired token")
        # RFC 8707 audience binding: the token must name THIS service in
        # its resource indicator (the hub put us in `aud`). This is core's
        # gate, not a scope. `aud` is upstream-controlled and may be a bare
        # string, so normalize to a list (a naive `in` on a string is a
        # substring test); compare trailing-slash normalized and log a
        # mismatch (a config error miserable to read off a 401).
        aud = claims.get("aud")
        audiences = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
        if settings.CORE_AUDIENCE not in [str(a).rstrip("/") for a in audiences]:
            logger.warning("token audience mismatch: expected %r, token carries %r", settings.CORE_AUDIENCE, audiences)
            raise AuthenticationFailed("token is not for this service")
        if not claims.get("sub") or not claims.get("account_id"):
            # 403, not 401: the credential is VALID (a mid-signup token
            # legitimately has no account), and a 401 would send a client
            # into a refresh that cannot help.
            raise PermissionDenied("token subject has no account")
        return AppUser.from_tokeninfo(claims), token

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="openbower-core"'

    @staticmethod
    def _check_miss_budget(request: Request) -> None:
        """Per-IP budget on verify CACHE MISSES, before the upstream call:
        DRF throttles run after auth, so they cannot stop a
        fresh-random-bearer flood from becoming a 1:1 verify amplifier.
        Legitimate callers miss only on token rotation, so the budget is
        generous and invisible."""
        ip = request.META.get("REMOTE_ADDR", "") or "unknown"
        key = f"{_MISS_PREFIX}{ip}"
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=_MISS_WINDOW_SECONDS)
            count = 1
        if count > settings.TOKENINFO_MISS_LIMIT_PER_MINUTE:
            logger.warning("tokeninfo miss budget exceeded for %s", ip)
            raise Throttled(detail="too many unverified tokens")

    @staticmethod
    def _verify_cached(token: str, request: Request) -> dict:
        cache_key = f"{_CACHE_PREFIX}{hash_token(token)}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        MachineTokenAuthentication._check_miss_budget(request)
        claims = verify_token(token)
        if claims.get("active"):
            ttl = settings.TOKENINFO_CACHE_SECONDS
            exp = claims.get("exp")
            if isinstance(exp, int):
                ttl = max(0, min(ttl, exp - int(time.time())))
        else:
            ttl = _NEGATIVE_TTL_SECONDS
        if ttl > 0:
            cache.set(cache_key, claims, timeout=ttl)
        return claims

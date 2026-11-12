"""The claims cache: THE one module that names a key in the tokeninfo
cache and reads or writes it, so the cached claims, the index that finds
a token's claims by machine key, and the miss throttle can never drift
apart.

A dedicated cache alias (its own table, shared by every process of the
install), so verify churn never culls the default cache's pending OAuth
login states, and one forget covers every replica.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import caches
from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle

from openbower_kernel.hashing import hash_token

_cache = caches["tokeninfo"]

# The claims, by token fingerprint: the token itself is never a key.
_CLAIMS_PREFIX = "tokeninfo:"
# key id -> the claims' key, so a revoke can drop one machine key's
# claims without knowing its token.
_KEY_INDEX_PREFIX = "tokeninfo-key:"


def _claims_key(token: str) -> str:
    return f"{_CLAIMS_PREFIX}{hash_token(token)}"


def _index_key(key_id: str) -> str:
    return f"{_KEY_INDEX_PREFIX}{key_id}"


def get_claims(token: str) -> dict | None:
    return _cache.get(_claims_key(token))


def remember_claims(token: str, claims: dict, *, ttl: int) -> None:
    """Cache the identity service's claims for `ttl` seconds. Active
    claims that name their machine key are indexed by that id for the
    same `ttl`, so forgetting the key finds them; claims from an identity
    service that predates the key id are not indexed and expire on their
    own."""
    claims_key = _claims_key(token)
    _cache.set(claims_key, claims, timeout=ttl)
    key_id = claims.get("key_id")
    if claims.get("active") and isinstance(key_id, str) and key_id:
        _cache.set(_index_key(key_id), claims_key, timeout=ttl)


def forget_machine_key(key_id: str) -> None:
    """Drop the cached claims for one machine key, so a revoke takes
    effect on the next request instead of when the cache entry expires."""
    index_key = _index_key(key_id)
    claims_key = _cache.get(index_key)
    if claims_key:
        _cache.delete(claims_key)
    _cache.delete(index_key)


class TokeninfoMissThrottle(SimpleRateThrottle):
    """TOKENINFO_MISS_LIMIT_PER_MINUTE verify misses per address, DRF's own
    throttle in this module's cache (its history prunes by timestamp, so no
    cache's handling of a timeout can stretch the window). The authenticator
    asks it before the upstream call, since DRF runs its throttles after
    authentication."""

    cache = _cache
    scope = "tokeninfo_miss"

    def get_rate(self) -> str:
        # Read per check, not at import: a setting is the limit, and tests
        # override it.
        return f"{settings.TOKENINFO_MISS_LIMIT_PER_MINUTE}/min"

    def get_ident(self, request: Request) -> str:
        # REMOTE_ADDR, the un-spoofable floor, never DRF's X-Forwarded-For
        # reading: with no NUM_PROXIES it keys on the whole header, which a
        # flood rotates freely. Behind a proxy this is the proxy, a coarse
        # global limiter, which is the safe direction to fail.
        return request.META.get("REMOTE_ADDR", "") or "unknown"

    def get_cache_key(self, request: Request, view) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}

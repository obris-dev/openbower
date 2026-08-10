"""Short-lived OAuth login-state store.

Owns the cache namespace for the state -> PKCE-verifier bags that exist
only for the browser's authorize round-trip. No other code touches this
prefix; going through the store is what guarantees it. The only read is
destructive (`pop`), so a state is single-use by construction and a
replayed callback finds nothing to exchange.
"""

from __future__ import annotations

from django.core.cache import cache

from auth_client.constants import STATE_TTL_SECONDS

_PREFIX = "oauth_state:"


class OAuthStateStore:
    @staticmethod
    def _key(state: str) -> str:
        return f"{_PREFIX}{state}"

    @staticmethod
    def put(*, state: str, verifier: str) -> None:
        cache.set(OAuthStateStore._key(state), verifier, timeout=STATE_TTL_SECONDS)

    @staticmethod
    def pop(*, state: str) -> str | None:
        """Return and CONSUME the verifier; None if unknown or expired.

        Consumed before the caller's token exchange, so a replayed
        callback can't race a second exchange with the same verifier.
        The delete's boolean is the claim: two callbacks can both get()
        the same bag, but only the one whose delete() actually removed
        the key proceeds, so the consume is single-flight without a
        read-then-delete race.
        """
        key = OAuthStateStore._key(state)
        verifier = cache.get(key)
        if verifier is None:
            return None
        if not cache.delete(key):
            return None
        return verifier

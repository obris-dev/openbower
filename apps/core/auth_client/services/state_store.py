"""Short-lived OAuth login-state store.

Owns the cache namespace for the state -> login bags (the PKCE verifier
and the path to land on afterwards) that exist only for the browser's
authorize round-trip. No other code touches this
prefix; going through the store is what guarantees it. The only read is
destructive (`pop`), so a state is single-use by construction and a
replayed callback finds nothing to exchange.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache

from auth_client.constants import STATE_TTL_SECONDS

_PREFIX = "oauth_state:"


@dataclass(frozen=True)
class LoginState:
    """What one login round trip carries: the verifier the token exchange
    needs, and the same-origin path the browser lands on afterwards."""

    verifier: str
    return_path: str


class OAuthStateStore:
    @staticmethod
    def _key(state: str) -> str:
        return f"{_PREFIX}{state}"

    @staticmethod
    def put(*, state: str, verifier: str, return_path: str) -> None:
        bag = {"verifier": verifier, "return_path": return_path}
        cache.set(OAuthStateStore._key(state), bag, timeout=STATE_TTL_SECONDS)

    @staticmethod
    def pop(*, state: str) -> LoginState | None:
        """Return and CONSUME the bag; None if unknown or expired.

        Consumed before the caller's token exchange, so a replayed
        callback can't race a second exchange with the same verifier.
        The delete's boolean is the claim: two callbacks can both get()
        the same bag, but only the one whose delete() actually removed
        the key proceeds, so the consume is single-flight without a
        read-then-delete race.
        """
        key = OAuthStateStore._key(state)
        bag = cache.get(key)
        if bag is None:
            return None
        if not cache.delete(key):
            return None
        return LoginState(verifier=bag["verifier"], return_path=bag["return_path"])

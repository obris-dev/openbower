"""The wire layer: the identity hub's token-verify call, transport and
protocol failures normalized into a named 503 before anyone downstream
touches the body (caching and claims semantics stay in the auth layer,
they are security controls, not wire mechanics).

Core is NOT a confidential client and holds no secret: it verifies a
machine token by RELAYING it to the hub's /v1/auth/tokeninfo endpoint
(self-introspection), where the presented token IS the only credential.
Contrast /oauth/introspect/, an oracle over arbitrary tokens that demands
confidential-client auth; that one is reserved for the central data leaf,
which can hold a secret. So this call carries a Bearer header and nothing
else."""

from __future__ import annotations

import httpx
from django.conf import settings

from . import urls


class TokeninfoUnavailable(Exception):
    """The hub could not answer, so this request's token cannot be
    verified. Rendered as 503 (retryable, not the caller's fault),
    distinct from 401 (the token itself was judged invalid)."""


def verify_token(token: str) -> dict:
    """Relay `token` to the hub's tokeninfo endpoint as its own Bearer, and
    return the raw claims dict. No caller credential: the token authorizes
    the lookup of its own identity. Every failure mode is a named upstream
    error, never a raw exception."""
    try:
        response = httpx.get(
            urls.tokeninfo_url(),
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.AUTH_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise TokeninfoUnavailable(f"tokeninfo unreachable: {type(e).__name__}") from e
    if response.status_code != 200:
        # tokeninfo answers 200 for valid AND invalid tokens (the body's
        # `active` carries that); any non-200 is the hub itself in trouble.
        raise TokeninfoUnavailable(f"tokeninfo returned {response.status_code}")
    try:
        claims = response.json()
    except ValueError as e:
        raise TokeninfoUnavailable("tokeninfo returned a non-JSON body") from e
    if not isinstance(claims, dict):
        raise TokeninfoUnavailable("tokeninfo returned a non-object body")
    return claims

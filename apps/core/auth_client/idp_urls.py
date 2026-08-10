"""Every openbower-auth (IdP) URL this app calls or redirects to.

One module owns the upstream URL shapes, so a path change on the IdP is a
one-place edit here, and no call site hand-builds an endpoint from
settings. The base comes from OPENBOWER_AUTH_URL (env-swappable: the
local IdP in dev, auth.openbower.com hosted). Paths must match the IdP's
conf/urls.py; the OAuth ones keep their canonical trailing slash because
OAuth endpoint URLs are exact client configuration.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings


def _base() -> str:
    return settings.OPENBOWER_AUTH_URL


def authorize_url(params: dict[str, object]) -> str:
    """GET: starts the Authorization Code flow (browser navigation).

    `doseq` so a list-valued param (e.g. `resource`, an RFC 8707 resource
    indicator that may repeat) encodes as repeated `key=v1&key=v2` pairs
    rather than a single stringified list.
    """
    return f"{_base()}/oauth/authorize/?{urlencode(params, doseq=True)}"


def token_url() -> str:
    """POST: code exchange and refresh-token rotation."""
    return f"{_base()}/oauth/token/"


def revoke_token_url() -> str:
    """POST: token revocation."""
    return f"{_base()}/oauth/revoke_token/"


def me_url() -> str:
    """GET with a Bearer access token: the user's identity.

    Versioned by the IdP's OWN prefix (OPENBOWER_AUTH_API_VERSION), NOT this
    app's API_VERSION_PREFIX: they are separate services, so bumping the
    app's API version must not silently repoint this cross-service call."""
    return f"{_base()}/{settings.OPENBOWER_AUTH_API_VERSION}/auth/me"


def logout_url(next_url: str) -> str:
    """GET (browser navigation): RP-initiated logout, ends the IdP session
    and bounces to `next_url` (must be registered as a post-logout
    redirect URI on the IdP)."""
    return f"{_base()}/logout?{urlencode({'next': next_url})}"

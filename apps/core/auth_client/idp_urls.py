"""Every openbower-auth (IdP) URL this app calls or redirects to.

One module owns the upstream URL shapes, so a path change on the IdP is a
one-place edit here, and no call site hand-builds an endpoint from
settings.

TWO bases, by who dials the URL. OPENBOWER_AUTH_URL is the IdP's
identity, the origin the BROWSER navigates to (authorize, logout) and the
one the token names as its audience. OPENBOWER_AUTH_INTERNAL_URL carries
the calls THIS PROCESS makes (token exchange, revocation, /me) and
defaults to the identity, so the two differ only where the network path
does (a containerized app reaching the IdP through a gateway alias).

Both are env-swappable (the local IdP in dev, the hosted IdP in cloud).
Paths must match the IdP's conf/urls.py; the OAuth ones keep their
canonical trailing slash because OAuth endpoint URLs are exact client
configuration.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings


def _base() -> str:
    """The IdP's canonical origin: URLs the BROWSER navigates to."""
    return settings.OPENBOWER_AUTH_URL


def _internal_base() -> str:
    """Transport for calls THIS PROCESS makes to the IdP. Distinct from
    _base() because a containerized app's network path to the IdP can
    differ from the browser's; identical to it everywhere else."""
    return settings.OPENBOWER_AUTH_INTERNAL_URL


def authorize_url(params: dict[str, object]) -> str:
    """GET: starts the Authorization Code flow (browser navigation).

    `doseq` so a list-valued param (e.g. `resource`, an RFC 8707 resource
    indicator that may repeat) encodes as repeated `key=v1&key=v2` pairs
    rather than a single stringified list.
    """
    return f"{_base()}/oauth/authorize/?{urlencode(params, doseq=True)}"


def token_url() -> str:
    """POST: code exchange and refresh-token rotation."""
    return f"{_internal_base()}/oauth/token/"


def revoke_token_url() -> str:
    """POST: token revocation."""
    return f"{_internal_base()}/oauth/revoke_token/"


def me_url() -> str:
    """GET with a Bearer access token: the user's identity.

    Versioned by the IdP's OWN prefix (OPENBOWER_AUTH_API_VERSION), NOT this
    app's API_VERSION_PREFIX: they are separate services, so bumping the
    app's API version must not silently repoint this cross-service call."""
    return f"{_internal_base()}/{settings.OPENBOWER_AUTH_API_VERSION}/auth/me"


def logout_url(next_url: str) -> str:
    """GET (browser navigation): RP-initiated logout, ends the IdP session
    and bounces to `next_url` (must be registered as a post-logout
    redirect URI on the IdP)."""
    return f"{_base()}/logout?{urlencode({'next': next_url})}"

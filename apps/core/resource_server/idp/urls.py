"""The identity-provider URLs this resource server calls. One module
owns the upstream shapes; the base is OPENBOWER_AUTH_INTERNAL_URL (the
transport this process dials), mirroring the data leaf."""

from __future__ import annotations

from django.conf import settings


def tokeninfo_url() -> str:
    """GET: the hub's self-introspection endpoint. The presented token is
    the only credential (see transport.verify_token), so no client auth."""
    return f"{settings.OPENBOWER_AUTH_INTERNAL_URL}/{settings.OPENBOWER_AUTH_API_VERSION}/auth/tokeninfo"

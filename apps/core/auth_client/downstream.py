"""The seam between the app and the downstream resource servers it calls
on a user's behalf.

`DownstreamTokenRejected` is the generic "the resource server said our
forwarded access token is inactive / expired / revoked" signal (an upstream
401, surfaced after the transport's refresh-and-retry could not clear it).
It lives here, not in a specific resource client, so the session layer and
the proxy views can catch it without depending on any one client, and
every resource client raises the SAME exception for the same condition.

It is distinct from a scope/authorization denial (an upstream 403): that means
the token is valid but not permitted, which a token refresh cannot fix (the
refreshed grant carries the same scopes), so that stays a client-specific
error the view maps to re-login.
"""

from __future__ import annotations


class DownstreamTokenRejected(Exception):
    """A downstream resource server rejected the forwarded access token as
    inactive/expired/revoked (upstream 401). Retryable after a token refresh;
    only if a freshly refreshed token is ALSO rejected is re-login warranted."""

"""The session credential as an httpx.Auth flow.

The industry shape for on-behalf-of-a-user clients: the HTTP layer itself
attaches the token and owns the 401-refresh-retry, so endpoint code and
callers carry no credential plumbing at all.

The flow mirrors the session rotation service's contract exactly:
  - attach the session's cached access token;
  - on a 401 (the resource server rejected it as inactive), rotate ONCE
    via force_refresh, which is single-flight by token identity (a peer
    that already rotated hands us its fresh token without spending
    another refresh), revokes the session on a terminal rejection, and
    PROPAGATES transient IdP failures (the caller maps them to 503,
    never to re-login);
  - retry with the fresh token; a second 401 stands and surfaces from
    the transport's status mapping as DownstreamTokenRejected (re-login).
"""

from __future__ import annotations

import httpx

from auth_client.services.sessions import AppSessionService


class SessionTokenAuth(httpx.Auth):
    def __init__(self, session) -> None:
        self._session = session

    def auth_flow(self, request: httpx.Request):
        # Read the token per attempt, not at construction: force_refresh
        # locks and updates the ROW, and a peer may rotate between calls.
        token = self._session.access_token
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code != 401:
            return
        refreshed = AppSessionService.Global.force_refresh(self._session, stale_token=token)
        if refreshed is None:
            # Session dead (terminal rejection upstream): let the 401
            # stand; the transport maps it to the re-login signal.
            return
        request.headers["Authorization"] = f"Bearer {refreshed.access_token}"
        yield request

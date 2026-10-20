"""The app-session package: `service` owns the cookie <-> session
lifecycle, `rotation` the token state machine underneath it, `token_auth`
the httpx flow that spends the session's token on a downstream call. This
surface is the public one; reach inside only in tests."""

from .service import AppSessionGlobal, AppSessionService
from .token_auth import SessionTokenAuth

__all__ = ["AppSessionGlobal", "AppSessionService", "SessionTokenAuth"]

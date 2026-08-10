"""The app-session package: `service` owns the cookie <-> session
lifecycle, `rotation` the token state machine underneath it. This surface
is the public one; reach inside only in tests."""

from .service import AppSessionGlobal, AppSessionService

__all__ = ["AppSessionGlobal", "AppSessionService"]

"""The account-scoped API view base the session-authed domains
subclass (auth_client deliberately owns its own auth posture)."""

from __future__ import annotations

from rest_framework import permissions
from rest_framework.views import APIView


class ScopedView(APIView):
    """Session-authed, account-scoped base for the domains that opt
    in. Subclasses attach their services as cached_property (DRF
    builds a fresh view instance per dispatch, so cached_property is
    exactly request lifetime) plus their or-404 helpers."""

    permission_classes = [permissions.IsAuthenticated]

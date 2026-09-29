"""`/v1/waitlist`: the one public, unauthenticated endpoint on this
API. DRF authentication is OFF (a visitor with a dead session cookie
must not 401 while joining a list), an IP throttle bounds the open
form, and the answer is identical for a new and a known address."""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from openbower_schema.waitlist import WaitlistSignupWire

from .constants import WAITLIST_THROTTLE_SCOPE
from .serializers import WaitlistSignupRequest
from .services import WaitlistService


class WaitlistSignupView(APIView):
    authentication_classes: list = []
    permission_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = WAITLIST_THROTTLE_SCOPE

    def post(self, request: Request) -> Response:
        body = WaitlistSignupRequest(data=request.data)
        body.is_valid(raise_exception=True)
        result = WaitlistService.signup(email=body.validated_data["email"], source=body.validated_data["source"])
        wire = WaitlistSignupWire(email=result.signup.email)
        return Response(wire.model_dump())

"""How a push endpoint answers a refused push: one map from accept_push's
refusals to their status and {error, detail} body, shared by the sheets
and signals push endpoints, so a push reads the same whichever took it."""

from __future__ import annotations

from rest_framework.response import Response

from ..constants import IngestErrorCode
from ..operations.append_rows import SheetNotAppendable
from .accept import PushInvalid, PushTooBig
from .publisher import IngestPublishError

PUSH_REFUSALS = (SheetNotAppendable, PushInvalid, PushTooBig, IngestPublishError)

# Never reached the bus, so not accepted: a retryable 503, never a 202 that
# dropped it, and never the broker's own words. The event id makes the
# retry safe.
_UNAVAILABLE = {"error": IngestErrorCode.INGEST_UNAVAILABLE, "detail": "ingest bus unavailable; retry"}


def push_refusal(exc: Exception) -> Response:
    """The answer to one of PUSH_REFUSALS."""
    if isinstance(exc, IngestPublishError):
        return Response(_UNAVAILABLE, status=503)
    status = 400 if isinstance(exc, PushInvalid) else 409
    return Response({"error": exc.code, "detail": str(exc)}, status=status)

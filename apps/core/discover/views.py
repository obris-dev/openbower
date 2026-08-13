"""`POST /v1/discover/lookalikes`: the app-session-authed look-alike proxy.

The browser holds only the `bwr_session` cookie; this view swaps it for
the session's IdP access token (already silently refreshed by the session
resolve in authentication) and forwards the query to the data service.
Only the known query keys are forwarded, and the response is the shared
LookalikeListResponse contract, re-serialized from the validated model.

Failure mapping: the data service's own 400 passes through as a 400 (its
`{error, detail}` body is already on contract); our token being rejected
maps to 403 `data_access_denied` (remedy: log in again, so the token
carries data:read); transient upstream trouble maps to 503
`data_unavailable`; anything else upstream-shaped is a 502 `data_error`.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.oauth import AuthUpstreamUnavailable
from discover.services.index_client import (
    IndexAccessDenied,
    IndexClientRequestError,
    IndexClientService,
    IndexUpstreamError,
    IndexUpstreamUnavailable,
)
from openbower_schema import LookalikeListResponse

from .constants import (
    MAX_INLINE_DOMAINS,
    MAX_LIMIT,
    MIN_INLINE_DOMAINS,
    RUN_STATUS_CANCELED,
    RUN_STATUS_COMPLETE,
    RUN_STATUS_FAILED,
    DiscoverErrorCode,
)

logger = logging.getLogger(__name__)


class LookalikeProxyRequest(serializers.Serializer):
    """The known query keys; anything else is dropped, semantics are the
    data service's to judge. The bounded inputs mirror the data service's own
    (it re-validates; these just stop oversized bodies at the door). `cursor`
    is deliberately UNbounded: it is an opaque value the data service itself
    minted, so the proxy must not reject a cursor data would accept (a future
    engine's cursor could exceed any cap we picked)."""

    domains = serializers.ListField(
        child=serializers.CharField(max_length=253),
        required=False,
        allow_empty=False,
        min_length=MIN_INLINE_DOMAINS,
        max_length=MAX_INLINE_DOMAINS,
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=MAX_LIMIT)
    cursor = serializers.CharField(required=False)


_UPSTREAM_ERRORS = (
    IndexClientRequestError,
    DownstreamTokenRejected,
    IndexAccessDenied,
    IndexUpstreamUnavailable,
    AuthUpstreamUnavailable,
    IndexUpstreamError,
)


def _error_response(e: Exception) -> Response:
    """The discover proxy's uniform failure mapping, shared by the JSON
    views and the CSV export."""
    if isinstance(e, IndexClientRequestError):
        # The data service's own status (400/404) + {error, detail} body,
        # passed through so a real "bad query" or "unknown seed set/run" is
        # not masked as a generic upstream failure.
        return Response(e.body, status=e.status)
    if isinstance(e, (DownstreamTokenRejected, IndexAccessDenied)):
        # Either a freshly refreshed token was STILL rejected / the session
        # is dead (DownstreamTokenRejected), or the token is valid but lacks
        # the data:read scope a refresh cannot add (IndexAccessDenied).
        # Neither is fixable in-band: the remedy is a fresh login.
        return Response(
            {
                "error": str(DiscoverErrorCode.DATA_ACCESS_DENIED),
                "detail": "the session cannot currently access the data service; try again or log in",
            },
            status=403,
        )
    if isinstance(e, (IndexUpstreamUnavailable, AuthUpstreamUnavailable)):
        # The data service, or the IdP during a token refresh, was
        # unreachable: an upstream problem, not a credential one.
        return Response(
            {"error": str(DiscoverErrorCode.DATA_UNAVAILABLE), "detail": "data service unavailable"},
            status=503,
        )
    return Response(
        {"error": str(DiscoverErrorCode.DATA_ERROR), "detail": "data service returned an invalid response"},
        status=502,
    )


def proxy_view(view_method):
    """Marks a view method as a data-service proxy: the method builds the
    request and
    returns the client's CONTRACT MODEL; this wrapper owns the uniform
    failure mapping and the translation to HTTP. A LookalikeListResponse
    gets the run-lifecycle translation: complete/canceled -> 200 (both
    terminal); pending/running -> 202 (keep polling); failed -> 502
    data_error (a failed COMPUTE is an upstream fault from the web's
    point of view, and the data service self-heals the run on the next
    query). Any other contract model is a plain 200. A view that returns
    a Response itself (its own validation 400) passes through untouched."""

    @functools.wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        try:
            result = view_method(self, request, *args, **kwargs)
        except _UPSTREAM_ERRORS as e:
            return _error_response(e)
        if isinstance(result, Response):
            return result
        if isinstance(result, LookalikeListResponse):
            if result.status == RUN_STATUS_FAILED:
                # The run's own detail is the data service's INTERNAL error
                # text (raw exception strings); log it for the operator,
                # never forward it to the browser.
                logger.warning("look-alike run %s failed upstream: %s", result.run_id, result.detail)
                return Response(
                    {"error": str(DiscoverErrorCode.DATA_ERROR), "detail": "look-alike computation failed"},
                    status=502,
                )
            http_status = 200 if result.status in (RUN_STATUS_COMPLETE, RUN_STATUS_CANCELED) else 202
            return Response(result.model_dump(), status=http_status)
        return Response(result.model_dump())

    return wrapper


class LookalikesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @proxy_view
    def post(self, request) -> LookalikeListResponse:
        serializer = LookalikeProxyRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload: dict[str, Any] = dict(serializer.validated_data)
        # The session-bound client owns the token plumbing: it forwards the
        # session's access token and, if the data service rejects it as
        # expired/revoked, refreshes once and retries, so an in-flight
        # token lapse never surfaces to the user.
        client = IndexClientService.for_session(request.user.session)
        return client.lookalikes(payload=payload)


class LookalikeRunCancelView(APIView):
    """POST /v1/discover/lookalikes/runs/{id}/cancel: stop the run. Pass
    -through of the data service's cancel (which answers with the same
    envelope as the poll, status now canceled or the terminal state it
    already reached)."""

    permission_classes = [permissions.IsAuthenticated]

    @proxy_view
    def post(self, request, id: str) -> LookalikeListResponse:
        client = IndexClientService.for_session(request.user.session)
        return client.cancel_run(run_id=id)


class LookalikeRunView(APIView):
    """GET /v1/discover/lookalikes/runs/{id}: the poll leg of the async
    lifecycle, same session auth + failure mapping as the query."""

    permission_classes = [permissions.IsAuthenticated]

    @proxy_view
    def get(self, request, id: str) -> LookalikeListResponse:
        # Forward the caller's page size: without it a cold cohort's first
        # page (served by this poll) and a warm one's (served inline by the
        # POST) would differ in length.
        raw_limit = request.query_params.get("limit")
        limit = None
        if raw_limit is not None:
            limit = serializers.IntegerField(min_value=1, max_value=MAX_LIMIT).run_validation(raw_limit)
        client = IndexClientService.for_session(request.user.session)
        return client.run_status(run_id=id, limit=limit)

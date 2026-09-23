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
`data_unavailable`; anything else upstream-shaped is a 502 `data_error`;
losing a race with a concurrent action (the save-list target deleted
mid-drain) is a 409 `conflict`.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from auth_client.downstream import DownstreamTokenRejected
from auth_client.services.oauth import AuthUpstreamUnavailable
from common.views import ScopedView
from discover.services.index_client import (
    IndexAccessDenied,
    IndexClientRequestError,
    IndexClientService,
    IndexUpstreamError,
    IndexUpstreamUnavailable,
)
from lists.constants import LABEL_MAX_LENGTH as LIST_LABEL_MAX_LENGTH
from lists.constants import MAX_LIST_ROWS, ColumnType, ListOrigin
from lists.serializers import list_wire
from lists.services.lists import ListNotFound, ListService, ListsFull
from openbower_kernel.domains import normalize_domain
from openbower_kernel.fields import is_valid_ulid
from openbower_schema import LookalikeListResponse
from openbower_schema.lists import PlainColumn

from .constants import (
    MAX_INLINE_DOMAINS,
    MAX_LIMIT,
    MAX_LIST_SEED_VALUES,
    MIN_INLINE_DOMAINS,
    RUN_STATUS_CANCELED,
    RUN_STATUS_COMPLETE,
    RUN_STATUS_FAILED,
    SAVE_LIST_PAGE,
    DiscoverErrorCode,
)
from .cursors import run_cursor

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
    # Seeding FROM A LIST is use-time interpretation of a column: the
    # caller names the list and which column holds the identifiers; this
    # view extracts + normalizes the values and forwards plain `domains`
    # (one seeding path; the data service resolves as it always does).
    list_id = serializers.CharField(max_length=26, required=False)
    identifier_key = serializers.CharField(max_length=40, required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=MAX_LIMIT)
    cursor = serializers.CharField(required=False)

    def validate(self, attrs: dict) -> dict:
        if bool(attrs.get("list_id")) != bool(attrs.get("identifier_key")):
            raise serializers.ValidationError("list_id and identifier_key go together")
        if attrs.get("list_id") and attrs.get("domains"):
            raise serializers.ValidationError("seed with domains OR a list, not both")
        return attrs


_UPSTREAM_ERRORS = (
    IndexClientRequestError,
    DownstreamTokenRejected,
    IndexAccessDenied,
    IndexUpstreamUnavailable,
    AuthUpstreamUnavailable,
    IndexUpstreamError,
)


def _error_response(e: Exception) -> Response:
    """The discover proxy's uniform failure mapping, shared by every
    view that talks to the data service."""
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


class LookalikesView(ScopedView):
    @proxy_view
    def post(self, request) -> LookalikeListResponse:
        serializer = LookalikeProxyRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload: dict[str, Any] = dict(serializer.validated_data)
        list_id = payload.pop("list_id", None)
        identifier_key = payload.pop("identifier_key", None)
        if list_id:
            domains = _list_seed_domains(request, list_id=list_id, identifier_key=identifier_key)
            if isinstance(domains, Response):
                return domains
            payload["domains"] = domains
        # The session-bound client owns the token plumbing: it forwards the
        # session's access token and, if the data service rejects it as
        # expired/revoked, refreshes once and retries, so an in-flight
        # token lapse never surfaces to the user.
        client = IndexClientService.for_session(request.auth)
        return client.lookalikes(payload=payload)


def _list_seed_domains(request, *, list_id: str, identifier_key: str) -> list[str] | Response:
    """The chosen column's values, normalized and deduped in first-seen
    order, capped at MAX_INLINE_DOMAINS. A sheet whose column yields
    fewer than MIN_INLINE_DOMAINS usable values answers a clear message
    (degrade, never error: content-agnostic sheets are allowed to hold
    no domains at all)."""
    from .domains_input import normalize_seed_values

    service = ListService(account_id=request.user.account_id)
    try:
        target = service.get(list_id)
    except ListNotFound:
        return _invalid_request("no list with that id")
    if identifier_key not in {c.key for c in target.columns}:
        return _invalid_request("that column does not exist on the list")
    values = service.column_values(target, key=identifier_key, limit=MAX_LIST_SEED_VALUES)
    domains = normalize_seed_values(values, cap=MAX_INLINE_DOMAINS)
    if len(domains) < MIN_INLINE_DOMAINS:
        return _invalid_request(f"that column yields fewer than {MIN_INLINE_DOMAINS} usable domains to seed from")
    return domains


def _invalid_request(detail: str) -> Response:
    return Response({"error": str(DiscoverErrorCode.INVALID_REQUEST), "detail": detail}, status=400)


class SaveListRequest(serializers.Serializer):
    """The save-list body. A serializer, not hand parsing: a non-object
    body, an oversized label, or an absurd limit must answer 400, never
    reach int() or the database. The limit cap is the row cap: asking
    for more than a list can hold means "everything"."""

    label = serializers.CharField(max_length=LIST_LABEL_MAX_LENGTH)
    limit = serializers.IntegerField(required=False, min_value=1)

    def validate_limit(self, value: int) -> int:
        # Clamp, never reject: past the row cap "means everything", and
        # the web sends the run's own cutoff, which may exceed it.
        return min(value, MAX_LIST_ROWS)

    # The web's Exclude filter: the saved sheet must match the table the
    # user is looking at, so the filtered-out domains ride along.
    exclude = serializers.ListField(
        child=serializers.CharField(max_length=253), required=False, max_length=MAX_INLINE_DOMAINS, default=list
    )


class LookalikeRunSaveListView(ScopedView):
    """POST /v1/discover/lookalikes/runs/{id}/save-list {label, limit?}:
    snapshot a COMPLETE run into a local sheet by paging the data
    service. The run lives upstream; the rows land here, where lists
    live. A full list stops the snapshot honestly (what fit is saved); an
    upstream failure mid-save deletes the partial list rather than
    leaving a half-sheet that looks finished; a concurrent delete of the
    target answers 409."""

    # Shared across requests: the members are frozen, so the field
    # holding these same instances on every saved sheet is safe.
    _COLUMNS = [
        PlainColumn(key="domain", label="Domain", type=ColumnType.URL),
        PlainColumn(key="name", label="Name", type=ColumnType.TEXT),
        PlainColumn(key="industry", label="Industry", type=ColumnType.TEXT),
        PlainColumn(key="size", label="Size", type=ColumnType.TEXT),
        PlainColumn(key="score", label="Score", type=ColumnType.NUMBER),
        PlainColumn(key="group", label="Group", type=ColumnType.TEXT),
    ]

    def post(self, request, id: str) -> Response:
        # The run id lands in origin_ref (varchar 64) and the upstream
        # cursor; a malformed one is a 404, not a database error.
        if not is_valid_ulid(id):
            raise NotFound("no run with that id")
        serializer = SaveListRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        label = serializer.validated_data["label"]
        wanted = serializer.validated_data.get("limit")
        excluded = {d for d in (normalize_domain(v) for v in serializer.validated_data["exclude"]) if d}
        client = IndexClientService.for_session(request.auth)
        service = ListService(account_id=request.user.account_id)
        target = service.create(
            owner_id=request.user.id,
            label=label,
            columns=self._COLUMNS,
            origin=ListOrigin.DISCOVER,
            origin_ref=id,
        )
        added = 0
        # `wanted` counts ranks WALKED (pre-exclusion), matching the
        # results table: the cutoff decides the set, exclusion then
        # removes from it.
        taken = 0
        cursor: str | None = run_cursor(id)
        # False on ANY exit but the two honest ones (snapshot done, list
        # full): the finally deletes the partial so no half-sheet can
        # survive a mid-save crash looking finished.
        completed = False
        try:
            while cursor is not None and (wanted is None or taken < wanted):
                # Clamp to remaining capacity so the final page part-fills
                # the sheet ("what fit is saved") instead of tripping
                # ListsFull and dropping whole.
                capacity = MAX_LIST_ROWS - added
                if capacity <= 0:
                    break
                limit = (
                    min(SAVE_LIST_PAGE, capacity) if wanted is None else min(SAVE_LIST_PAGE, capacity, wanted - taken)
                )
                result = client.lookalikes(payload={"limit": limit, "cursor": cursor})
                if result.status != RUN_STATUS_COMPLETE:
                    return _invalid_request("run is not complete; poll it before saving")
                # Slice to what was asked: the caps must hold even if
                # the upstream answers more than the requested limit.
                items = (result.items or [])[:limit]
                if not items:
                    break
                taken += len(items)
                rows = [
                    {
                        "domain": item.company.domain,
                        "name": item.company.name,
                        "industry": item.company.industry or "",
                        "size": item.company.size_band or "",
                        "score": str(item.score),
                        "group": item.group or "",
                    }
                    for item in items
                    # Normalized on BOTH sides: the upstream's form is
                    # canonical today, but the compare must not depend
                    # on that staying true.
                    if normalize_domain(item.company.domain) not in excluded
                ]
                # A snapshot is a bulk load, not a request to fill: the
                # workflow is not triggered for its rows; Fill is the consent.
                added += len(service.add_rows(target, rows))
                # The advance guard the client export also carries: a
                # stuck cursor must not walk forever.
                if result.next_cursor == cursor:
                    raise IndexUpstreamError("run cursor did not advance")
                cursor = result.next_cursor
            completed = True
        except ListsFull:
            # A concurrent writer filled the list first: what fit is
            # saved, honestly partial.
            completed = True
        except ListNotFound:
            # The target was deleted mid-save: a conflict with a
            # concurrent action, not a bad request.
            return Response(
                {"error": str(DiscoverErrorCode.CONFLICT), "detail": "the list was deleted during the save"},
                status=409,
            )
        except _UPSTREAM_ERRORS as e:
            return _error_response(e)
        finally:
            if not completed:
                service.delete(target)
        target.refresh_from_db()
        return Response(list_wire(target), status=201)


class LookalikeRunCancelView(ScopedView):
    """POST /v1/discover/lookalikes/runs/{id}/cancel: stop the run. Pass
    -through of the data service's cancel (which answers with the same
    envelope as the poll, status now canceled or the terminal state it
    already reached)."""

    @proxy_view
    def post(self, request, id: str) -> LookalikeListResponse:
        client = IndexClientService.for_session(request.auth)
        return client.cancel_run(run_id=id)


class LookalikeRunView(ScopedView):
    """GET /v1/discover/lookalikes/runs/{id}: the poll leg of the async
    lifecycle, same session auth + failure mapping as the query."""

    @proxy_view
    def get(self, request, id: str) -> LookalikeListResponse:
        # Forward the caller's page size: without it a cold cohort's first
        # page (served by this poll) and a warm one's (served inline by the
        # POST) would differ in length.
        raw_limit = request.query_params.get("limit")
        limit = None
        if raw_limit is not None:
            limit = serializers.IntegerField(min_value=1, max_value=MAX_LIMIT).run_validation(raw_limit)
        client = IndexClientService.for_session(request.auth)
        return client.run_status(run_id=id, limit=limit)

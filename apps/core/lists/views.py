"""`/v1/lists`: the sheets CRUD + rows + CSV import.

Session-authed like discover; every query scopes through the caller's
account via the service (foreign ids read as not-found). No export
endpoint on purpose: exports build client-side from the rows pages."""

from __future__ import annotations

import logging
from functools import cached_property

import ulid
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from agents.services import AgentNotFound
from auth_client.authentication import AppSessionAuthentication
from common.views import ScopedView
from openbower_kernel.pagination import next_cursor_from, parse_limit
from openbower_schema.agents import AgentConfig
from openbower_schema.fills import ColumnPromptWire, FillRunPage
from openbower_schema.lists import (
    FoldersList,
    ImportResult,
    IngestAccepted,
    ListRowsPage,
    ListsPage,
    PlainColumn,
    RowsAdded,
)
from openbower_schema.webhooks import WebhookColumnPreviewResponse, WebhookColumnTestResponse
from resource_server import MachineTokenAuthentication
from webhooks.serializers import delivery_model

from .constants import (
    DEFAULT_INDEX_PAGE,
    DEFAULT_ROWS_PAGE,
    IMPORT_BODY_OVERHEAD,
    LABEL_MAX_LENGTH,
    MAX_CSV_BYTES,
    MAX_INDEX_PAGE,
    MAX_ROWS_PAGE,
    FillErrorCode,
    IngestErrorCode,
    ListOrigin,
)
from .ingest import IngestEvent, IngestPublishError, get_ingest_publisher
from .models import Folder, List
from .operations.import_csv import CsvTooLarge, CsvUnusable, ImportCsvOperation
from .serializers import (
    AiColumnRequest,
    ColumnAddRequest,
    ColumnOrderRequest,
    ColumnPromptRequest,
    ColumnRefillRequest,
    ColumnRenameRequest,
    FolderRequest,
    IngestRequest,
    ListCreateRequest,
    ListPatchRequest,
    RowsAddRequest,
    WebhookColumnAddRequest,
    WebhookColumnPatchRequest,
    WebhookColumnTestRequest,
    fill_run_wire,
    fill_runs_wire,
    folder_wire,
    ingest_schema_wire,
    list_wire,
    row_wire,
    validate_ingest_rows,
)
from .services.columns import ColumnNotFound, ColumnRefused, ColumnService
from .services.fill_admission import FillAdmissionService, FillColumnNotFound, FillRefused
from .services.fills import FillNotFound, FillService
from .services.lists import FolderNotFound, FolderService, FoldersFull, ListNotFound, ListService, ListsFull
from .services.webhook_columns import WebhookColumnRefused, WebhookColumnService
from .services.workflows import NodeNotFound

logger = logging.getLogger(__name__)

# Refusal codes that answer 409 (a conflict with live state: the same
# request succeeds once the world changes, with nothing for the caller
# to alter); every other FillRefused code is a 400 (the request itself
# must change, by narrowing the ask or by configuring the deployment).
# Both ride the sibling envelope ({error: <code>, detail}): the client
# classifies by CODE and renders the detail verbatim (tier 1).
_FILL_CONFLICT_CODES = frozenset({FillErrorCode.FILL_ACTIVE, FillErrorCode.FILLS_FULL})

# The same partition for the COLUMN vocabulary, kept separate because
# the two sets are disjoint and neither endpoint should classify by
# the other's codes.
_COLUMN_CONFLICT_CODES = frozenset({FillErrorCode.COLUMN_ORDER_STALE, FillErrorCode.COLUMN_WAITED_ON})


def _column_refusal_status(e: ColumnRefused) -> int:
    """One classifier for every column write, so a status added to the
    set reaches both endpoints and neither can drift from the other."""
    return 409 if e.code in _COLUMN_CONFLICT_CODES else 400


class _ScopedView(ScopedView):
    @cached_property
    def lists(self) -> ListService:
        return ListService(account_id=self.request.user.account_id)

    @cached_property
    def folders(self) -> FolderService:
        return FolderService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def columns(self) -> ColumnService:
        return ColumnService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def fill_admission(self) -> FillAdmissionService:
        return FillAdmissionService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def fills(self) -> FillService:
        return FillService(account_id=self.request.user.account_id)

    @cached_property
    def webhook_columns(self) -> WebhookColumnService:
        return WebhookColumnService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    def _list_or_404(self, list_id: str) -> List:
        try:
            return self.lists.get(list_id)
        except ListNotFound as e:
            raise NotFound("no list with that id") from e

    def _folder_or_404(self, folder_id: str) -> Folder:
        try:
            return self.folders.get(folder_id)
        except FolderNotFound as e:
            raise NotFound("no folder with that id") from e


class ListsView(_ScopedView):
    # The lists collection is where a machine producer both enumerates
    # ("which lists can I push to?") and creates a list to push into, so
    # BOTH methods ride the machine lane; account/user scoping (no session
    # state) makes a machine token safe on either. Every other lists
    # endpoint keeps the cookie-only default, which also proves the per-view
    # scoping. Cookie auth is listed FIRST so a browser's 401 (an expired
    # session) carries the `Cookie` challenge, not `Bearer`; the machine
    # class returns None without a Bearer header, so it still authenticates
    # a real machine token.
    authentication_classes = [AppSessionAuthentication, MachineTokenAuthentication]

    def get(self, request: Request) -> Response:
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        after = request.query_params.get("after", "")
        rows = self.lists.page(after_id=after, limit=limit)
        page = ListsPage(items=[list_wire(x) for x in rows], next_cursor=next_cursor_from(rows, limit=limit))
        return Response(page.model_dump())

    def post(self, request: Request) -> Response:
        serializer = ListCreateRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        columns = [PlainColumn(**column) for column in data["columns"]]
        target_list = self.lists.create(
            owner_id=self.request.user.id,
            label=data["label"],
            columns=columns,
            origin=ListOrigin.MANUAL,
        )
        return Response(list_wire(target_list), status=201)


class ListDetailView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        return Response(list_wire(self._list_or_404(id)))

    def patch(self, request: Request, id: str) -> Response:
        serializer = ListPatchRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target_list = self._list_or_404(id)
        # One PATCH, one outcome: a bad folder id must not leave a
        # half-applied rename behind.
        with transaction.atomic():
            if "label" in data:
                target_list = self.lists.rename(target_list, label=data["label"])
            if "folder_id" in data:
                try:
                    target_list = self.lists.move(target_list, folder_id=data["folder_id"])
                except FolderNotFound:
                    raise ValidationError("no folder with that id") from None
        return Response(list_wire(target_list))

    def delete(self, request: Request, id: str) -> Response:
        self.lists.delete(self._list_or_404(id))
        return Response(status=204)


class ListRowsView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        target_list = self._list_or_404(id)
        limit = parse_limit(request, default=DEFAULT_ROWS_PAGE, maximum=MAX_ROWS_PAGE)
        raw_after = request.query_params.get("after", "0")
        if not raw_after.isdecimal() or len(raw_after) > 9:
            raise ValidationError("?after= must be a row position")
        rows = self.lists.rows_page(target_list, after_position=int(raw_after), limit=limit)
        states = self.fills.cell_states_for_rows(target_list, rows)
        webhooks = self.webhook_columns.cell_states_for_rows(target_list, rows)
        next_cursor = str(rows[-1].position) if len(rows) == limit else None
        items = [row_wire(r, states.get(str(r.id), {}), webhooks.get(str(r.id), {})) for r in rows]
        page = ListRowsPage(items=items, next_cursor=next_cursor)
        return Response(page.model_dump())

    def post(self, request: Request, id: str) -> Response:
        target_list = self._list_or_404(id)
        serializer = RowsAddRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            added = len(self.lists.add_rows(target_list, serializer.validated_data["rows"]))
        except ListsFull as e:
            raise ValidationError(str(e)) from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        target_list.refresh_from_db()
        return Response(RowsAdded(added=added, row_count=target_list.row_count).model_dump(), status=201)


class ListIngestView(_ScopedView):
    """POST /v1/lists/{id}/ingest: the ASYNC row push (the webhook).

    A minted machine key (or a session) pushes rows; we validate them and
    the target list (account-scoped, so a key only reaches its owner's
    lists), publish the batch to the ingest bus, and return 202. The rows
    are NOT in the sheet on return: a worker appends them off the bus.

    INTERIM: the current publisher logs and drops (see lists.ingest); the
    durable backend (outbox, then Kafka) and the append worker are
    follow-ups. Same auth pair and order as the lists collection.
    """

    authentication_classes = [AppSessionAuthentication, MachineTokenAuthentication]

    def get(self, request: Request, id: str) -> Response:
        """GET /v1/lists/{id}/ingest: the pushable row schema, every column
        with its key and type, so a producer can build a push without
        guessing. AI columns carry autopopulated=true: a producer may leave
        them for autofill or send a value to pin its own. Same
        account-scoped auth as the push."""
        return Response(ingest_schema_wire(self._list_or_404(id)))

    def post(self, request: Request, id: str) -> Response:
        target_list = self._list_or_404(id)
        serializer = IngestRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Validate the push against the sheet's columns BEFORE accepting it:
        # the append is async (202), so the POST is the only place a
        # malformed value can reach the producer as a 400 it can fix, rather
        # than being stored as sent. Tier-1 envelope ({error, detail}). The
        # returned rows are run through the shared cells_for_storage (the
        # writers' transform), so the bus carries exactly what add_rows will
        # store (canonical form, clamped), not the raw push.
        problems, rows = validate_ingest_rows(target_list, serializer.validated_data["rows"])
        if problems:
            return Response({"error": IngestErrorCode.INGEST_INVALID, "detail": "; ".join(problems)}, status=400)
        # The caller's idempotency key if they sent one, else a fresh ULID.
        # Carried through the bus so a re-delivery dedupes to one append once
        # the durable backend enforces it (the interim publisher only logs).
        event = IngestEvent(
            event_id=serializer.validated_data.get("event_id") or str(ulid.ulid()),
            list_id=str(target_list.id),
            account_id=self.request.user.account_id,
            user_id=self.request.user.id,
            rows=rows,
            received_at=timezone.now(),
        )
        try:
            get_ingest_publisher().publish(event)
        except IngestPublishError as e:
            # The batch never reached the bus, so it was NOT accepted: 503
            # (retryable), never a 202 that silently dropped it. Dedupe makes
            # the caller's retry safe.
            logger.warning("ingest publish failed for list %s: %s", event.list_id, e)
            return Response(
                {"error": IngestErrorCode.INGEST_UNAVAILABLE, "detail": "ingest bus unavailable; retry"}, status=503
            )
        return Response(IngestAccepted(event_id=event.event_id, accepted=len(rows)).model_dump(), status=202)


class ColumnsView(_ScopedView):
    """POST /v1/lists/{id}/columns: append one BLANK column (the
    CSV-template flow, for values typed or pasted in; an AI fill
    REFUSES a key a column already holds). Refusals ride
    the sibling envelope: the client classifies by CODE and renders
    the detail verbatim (tier 1). The 200 body is the updated list
    summary, so the sheet re-renders its columns from the response."""

    def post(self, request: Request, id: str) -> Response:
        serializer = ColumnAddRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            target_list = self.columns.add_column(id, label=data["label"], column_type=data["type"])
        except ColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=_column_refusal_status(e))
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(list_wire(target_list))


class ColumnOrderView(_ScopedView):
    """PATCH /v1/lists/{id}/column-order {keys}: reorder the sheet's
    columns. The 200 body is the updated list summary, the same shape
    the sibling column writes return, so the sheet re-renders its
    columns from the response rather than trusting its own optimistic
    move."""

    def patch(self, request: Request, id: str) -> Response:
        serializer = ColumnOrderRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            target_list = self.columns.reorder(id, keys=serializer.validated_data["keys"])
        except ColumnRefused as e:
            # Caught at the BASE and classified by code, the same way
            # the sibling column write above does it: a refusal added
            # to reorder() later gets a status here instead of escaping
            # as a 500.
            return Response({"error": e.code, "detail": str(e)}, status=_column_refusal_status(e))
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(list_wire(target_list))


class AiColumnView(_ScopedView):
    """POST /v1/lists/{id}/columns/ai: add the column and admit its
    fill in the service's one transaction. The 201 body is the fill
    envelope the sheet re-attaches to."""

    def post(self, request: Request, id: str) -> Response:
        serializer = AiColumnRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = AgentConfig(**data["config"]) if data.get("config") is not None else None
        try:
            fill = self.fill_admission.admit(
                list_id=id,
                config=config,
                agent_id=data["agent_id"],
                confirmed_row_count=data["confirmed_row_count"],
                rows=data["rows"],
            )
        except FillRefused as e:
            status = 409 if e.code in _FILL_CONFLICT_CODES else 400
            return Response({"error": e.code, "detail": str(e)}, status=status)
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        except AgentNotFound as e:
            raise NotFound("no agent with that id") from e
        return Response(fill_run_wire(fill), status=201)


class ColumnWebhookTestView(_ScopedView):
    """POST /v1/lists/{id}/columns/webhook/test: one sample digest to a
    destination, sent now. Answers 200 with the delivery and the envelope
    it carried whatever the receiver did, since a failed delivery is an
    API object. Every refusal names something in the body the caller
    changes, so it is a 400 with a code."""

    def post(self, request: Request, id: str) -> Response:
        serializer = WebhookColumnTestRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            sent = self.webhook_columns.test(
                id,
                key=data["key"],
                destination_id=data["destination_id"],
                wait_keys=data["wait_keys"],
                payload_keys=data["payload_keys"],
                row_id=data["row_id"],
                cells=data["cells"],
            )
        except WebhookColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=400)
        except NodeNotFound as e:
            logger.warning("webhook column read: node gone (%s)", e)
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        delivery = delivery_model(sent.delivery)
        body = WebhookColumnTestResponse(delivery=delivery, envelope=sent.envelope)
        return Response(body.model_dump())


class ColumnWebhookPreviewView(_ScopedView):
    """POST /v1/lists/{id}/columns/webhook/preview: the envelope a test
    send of this body would carry, rendered and sent nowhere, so the
    sheet shows the truth before a send. The test route's body."""

    def post(self, request: Request, id: str) -> Response:
        serializer = WebhookColumnTestRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            envelope = self.webhook_columns.preview(
                id,
                key=data["key"],
                destination_id=data["destination_id"],
                wait_keys=data["wait_keys"],
                payload_keys=data["payload_keys"],
                row_id=data["row_id"],
                cells=data["cells"],
            )
        except WebhookColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=400)
        except NodeNotFound as e:
            logger.warning("webhook column read: node gone (%s)", e)
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        body = WebhookColumnPreviewResponse(envelope=envelope)
        return Response(body.model_dump())


class ColumnWebhookView(_ScopedView):
    """POST /v1/lists/{id}/columns/webhook: add a Send webhook column
    (its path and two nodes with it). 201 with the list summary, the
    shape every columns write returns. Body references refuse 400 with
    a code; the column's own name refuses as a column add does."""

    def post(self, request: Request, id: str) -> Response:
        serializer = WebhookColumnAddRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            target_list = self.webhook_columns.add(
                id,
                label=data["label"],
                destination_id=data["destination_id"],
                wait_keys=data["wait_keys"],
                payload_keys=data["payload_keys"],
                interval_seconds=data["interval_seconds"],
            )
        except WebhookColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=400)
        except NodeNotFound as e:
            logger.warning("webhook column read: node gone (%s)", e)
            raise NotFound("no column with that key") from e
        except ColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=_column_refusal_status(e))
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(list_wire(target_list), status=201)


class ColumnWebhookDetailView(_ScopedView):
    """GET /v1/lists/{id}/columns/{key}/webhook: the column as
    configured. PATCH: the whole config, rewritten. The key sits in its
    own segment, as the refill and prompt routes place it, so no column
    key can shadow a literal route (the `columns/ai` and
    `columns/webhook` collections are the reserved keys)."""

    def get(self, request: Request, id: str, key: str) -> Response:
        try:
            config = self.webhook_columns.config(id, key)
        except WebhookColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=400)
        except NodeNotFound as e:
            logger.warning("webhook column read: node gone (%s)", e)
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(config.model_dump())

    def patch(self, request: Request, id: str, key: str) -> Response:
        serializer = WebhookColumnPatchRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            config = self.webhook_columns.update(
                id,
                key,
                destination_id=data["destination_id"],
                wait_keys=data["wait_keys"],
                payload_keys=data["payload_keys"],
                interval_seconds=data["interval_seconds"],
                enabled=data["enabled"],
            )
        except WebhookColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=400)
        except NodeNotFound as e:
            logger.warning("webhook column read: node gone (%s)", e)
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(config.model_dump())


class ColumnDetailView(_ScopedView):
    """PATCH /v1/lists/{id}/columns/{key} {label}: relabel one column.
    DELETE: remove it and everything it holds.

    Delete takes ANY column, not only an AI one: a plain column is the
    same operation with less to clean up, and a sheet the user cannot
    tidy is the worse failure. The 200 body is the updated list
    summary, the shape every columns write returns."""

    def patch(self, request: Request, id: str, key: str) -> Response:
        serializer = ColumnRenameRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            target_list = self.columns.rename(id, key=key, label=serializer.validated_data["label"])
        except ColumnNotFound as e:
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(list_wire(target_list))

    def delete(self, request: Request, id: str, key: str) -> Response:
        try:
            target_list = self.columns.delete(id, key=key)
        except ColumnRefused as e:
            return Response({"error": e.code, "detail": str(e)}, status=_column_refusal_status(e))
        except ColumnNotFound as e:
            raise NotFound("no column with that key") from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        return Response(list_wire(target_list))


class ColumnRefillView(_ScopedView):
    """POST /v1/lists/{id}/columns/{key}/refill: the one recovery
    primitive, a NEW fill over the column's unanswered rows (the column
    names everything except the optional `rows` scope, and the service
    takes a fresh config snapshot). The 201 body is the fill envelope,
    exactly like the add."""

    def post(self, request: Request, id: str, key: str) -> Response:
        serializer = ColumnRefillRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            fill = self.fill_admission.refill(
                list_id=id,
                column_key=key,
                rows=serializer.validated_data["rows"],
                resume_fill_id=serializer.validated_data["resume_fill"],
                confirmed_row_count=serializer.validated_data["confirmed_row_count"],
            )
        except FillRefused as e:
            status = 409 if e.code in _FILL_CONFLICT_CODES else 400
            return Response({"error": e.code, "detail": str(e)}, status=status)
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        except FillColumnNotFound as e:
            raise NotFound("no fill column with that key") from e
        except AgentNotFound as e:
            raise NotFound("no agent with that id") from e
        return Response(fill_run_wire(fill), status=201)


class ColumnPromptView(_ScopedView):
    """GET and PATCH /v1/lists/{id}/columns/{key}/prompt: the column's
    CURRENT fill config (what a refill would run), and the
    prompt-only edit against it. Surfaces peeking at "what fills this
    column" read HERE. A fill reads its agent live, so an edit reaches
    a running fill's next row, and a refill re-targets every blank."""

    def get(self, request: Request, id: str, key: str) -> Response:
        try:
            config = self.columns.fill_config(id, column_key=key)
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        except FillColumnNotFound as e:
            raise NotFound("no fill column with that key") from e
        except AgentNotFound as e:
            raise NotFound("no agent with that id") from e
        return Response(_column_prompt_wire(config))

    def patch(self, request: Request, id: str, key: str) -> Response:
        serializer = ColumnPromptRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            stored = self.columns.update_fill_prompt(id, column_key=key, prompt=serializer.validated_data["prompt"])
        except FillRefused as e:
            status = 409 if e.code in _FILL_CONFLICT_CODES else 400
            return Response({"error": e.code, "detail": str(e)}, status=status)
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        except FillColumnNotFound as e:
            raise NotFound("no fill column with that key") from e
        except AgentNotFound as e:
            raise NotFound("no agent with that id") from e
        return Response(_column_prompt_wire(stored))


class ListFillsView(_ScopedView):
    """GET /v1/lists/{id}/fills?after=: the list's LIVE fill runs,
    keyset by -id, plus the per-column summaries the tracker renders
    (server truth: the totals, the newest run's status, and its error
    when it failed, never a 4xx; a client sum over one page of runs
    silently undercounts once history outgrows the page)."""

    def get(self, request: Request, id: str) -> Response:
        target_list = self._list_or_404(id)
        limit = parse_limit(request, default=DEFAULT_INDEX_PAGE, maximum=MAX_INDEX_PAGE)
        after = request.query_params.get("after", "")
        runs = list(self.fills.page_for_list(str(target_list.id), after_id=after, limit=limit))
        page = FillRunPage(
            runs=fill_runs_wire(runs),
            columns=self.fills.column_summaries(target_list),
            next_cursor=next_cursor_from(runs, limit=limit),
        )
        return Response(page.model_dump())


class FillCancelView(_ScopedView):
    def post(self, request: Request, id: str, fill_run_id: str) -> Response:
        target_list = self._list_or_404(id)
        try:
            fill = self.fills.get(fill_run_id)
        except FillNotFound as e:
            raise NotFound("no fill with that id") from e
        # The route nests under a list; a fill of another sheet must not
        # be addressable through this one's URL.
        if fill.subject_id != str(target_list.id):
            raise NotFound("no fill with that id")
        return Response(fill_run_wire(self.fills.cancel(fill_run_id)))


def _column_prompt_wire(config: AgentConfig) -> dict:
    return ColumnPromptWire(prompt=config.prompt, model=config.model, source=config.source).model_dump()


class ListImportView(_ScopedView):
    """POST multipart {file, label?}: a CSV becomes a plain sheet. The
    filename (sans extension) is the default label so a quick drop needs
    no form at all."""

    def post(self, request: Request) -> Response:
        # Reject on the DECLARED size before touching request.FILES:
        # reading FILES parses the whole multipart body, so the cap must
        # fire first or an oversized upload gets buffered just to be
        # refused. (A deployment still wants the reverse proxy capping
        # bodies; this is the app-side floor.)
        raw_length = str(request.META.get("CONTENT_LENGTH") or "0")
        # Some servers pass the header through unvalidated; junk reads
        # as absent rather than a 500.
        declared = int(raw_length) if raw_length.isdecimal() else 0
        if declared > MAX_CSV_BYTES + IMPORT_BODY_OVERHEAD:
            raise ValidationError(f"the file exceeds {MAX_CSV_BYTES // (1024 * 1024)}MB")
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("a CSV file upload is required")
        if upload.size > MAX_CSV_BYTES:
            raise ValidationError(f"the file exceeds {MAX_CSV_BYTES // (1024 * 1024)}MB")
        # Filename fallback, then a last resort: a file literally named
        # ".csv" must not create a nameless sheet.
        label = str(request.data.get("label", "")).strip() or upload.name.rsplit(".", 1)[0]
        label = label[:LABEL_MAX_LENGTH] or "Imported list"
        operation = ImportCsvOperation(
            account_id=request.user.account_id, user_id=request.user.id, label=label, raw=upload.read()
        )
        try:
            stats = operation.run()
        except (CsvUnusable, CsvTooLarge) as e:
            raise ValidationError(str(e)) from e
        wire = ImportResult(list=list_wire(stats.target), rows=stats.rows, skipped=stats.skipped)
        return Response(wire.model_dump(), status=201)


class FoldersView(_ScopedView):
    def get(self, request: Request) -> Response:
        # Folders are a small, unbounded-by-nothing user taxonomy; the
        # whole set ships (no paging, deliberately unlike the lists).
        folders = self.folders.all()
        counts = self.folders.list_counts([str(f.id) for f in folders])
        items = [folder_wire(f, list_count=counts.get(str(f.id), 0)) for f in folders]
        return Response(FoldersList(items=items).model_dump())

    def post(self, request: Request) -> Response:
        serializer = FolderRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            created = self.folders.create(label=serializer.validated_data["label"])
        except FoldersFull as e:
            raise ValidationError(str(e)) from e
        return Response(folder_wire(created, list_count=0), status=201)


class FolderDetailView(_ScopedView):
    def patch(self, request: Request, id: str) -> Response:
        serializer = FolderRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        folder = self.folders.rename(self._folder_or_404(id), label=serializer.validated_data["label"])
        count = self.folders.list_counts([str(folder.id)]).get(str(folder.id), 0)
        return Response(folder_wire(folder, list_count=count))

    def delete(self, request: Request, id: str) -> Response:
        self.folders.delete(self._folder_or_404(id))
        return Response(status=204)

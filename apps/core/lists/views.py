"""`/v1/lists`: the sheets CRUD + rows + CSV import.

Session-authed like discover; every query scopes through the caller's
account via the service (foreign ids read as not-found). No export
endpoint on purpose: exports build client-side from the rows pages."""

from __future__ import annotations

from functools import cached_property

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from common.views import ScopedView
from openbower_kernel.pagination import next_cursor_from, parse_limit
from openbower_schema.lists import FoldersList, ImportResult, ListRowsPage, ListsPage, RowsAdded

from .constants import (
    DEFAULT_INDEX_PAGE,
    DEFAULT_ROWS_PAGE,
    IMPORT_BODY_OVERHEAD,
    LABEL_MAX_LENGTH,
    MAX_CSV_BYTES,
    MAX_INDEX_PAGE,
    MAX_ROWS_PAGE,
    ListOrigin,
)
from .models import Folder, List
from .operations.import_csv import CsvTooLarge, CsvUnusable, ImportCsvOperation
from .serializers import (
    FolderRequest,
    ListCreateRequest,
    ListPatchRequest,
    RowsAddRequest,
    folder_wire,
    list_wire,
    row_wire,
)
from .services.lists import FolderNotFound, FolderService, FoldersFull, ListNotFound, ListService, ListsFull


class _ScopedView(ScopedView):
    @cached_property
    def lists(self) -> ListService:
        return ListService(account_id=self.request.user.account_id, user_id=self.request.user.id)

    @cached_property
    def folders(self) -> FolderService:
        return FolderService(account_id=self.request.user.account_id, user_id=self.request.user.id)

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
        target = self.lists.create(label=data["label"], columns=data["columns"], origin=ListOrigin.MANUAL)
        return Response(list_wire(target), status=201)


class ListDetailView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        return Response(list_wire(self._list_or_404(id)))

    def patch(self, request: Request, id: str) -> Response:
        serializer = ListPatchRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = self._list_or_404(id)
        # One PATCH, one outcome: a bad folder id must not leave a
        # half-applied rename behind.
        with transaction.atomic():
            if "label" in data:
                target = self.lists.rename(target, label=data["label"])
            if "folder_id" in data:
                try:
                    target = self.lists.move(target, folder_id=data["folder_id"])
                except FolderNotFound:
                    raise ValidationError("no folder with that id") from None
        return Response(list_wire(target))

    def delete(self, request: Request, id: str) -> Response:
        self.lists.delete(self._list_or_404(id))
        return Response(status=204)


class ListRowsView(_ScopedView):
    def get(self, request: Request, id: str) -> Response:
        target = self._list_or_404(id)
        limit = parse_limit(request, default=DEFAULT_ROWS_PAGE, maximum=MAX_ROWS_PAGE)
        raw_after = request.query_params.get("after", "0")
        if not raw_after.isdecimal() or len(raw_after) > 9:
            raise ValidationError("?after= must be a row position")
        rows = self.lists.rows_page(target, after_position=int(raw_after), limit=limit)
        next_cursor = str(rows[-1].position) if len(rows) == limit else None
        page = ListRowsPage(items=[row_wire(r) for r in rows], next_cursor=next_cursor)
        return Response(page.model_dump())

    def post(self, request: Request, id: str) -> Response:
        target = self._list_or_404(id)
        serializer = RowsAddRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            added = self.lists.add_rows(target, serializer.validated_data["rows"])
        except ListsFull as e:
            raise ValidationError(str(e)) from e
        except ListNotFound as e:
            raise NotFound("no list with that id") from e
        target.refresh_from_db()
        return Response(RowsAdded(added=added, row_count=target.row_count).model_dump(), status=201)


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

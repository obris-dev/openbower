"""Account-scoped READS of cell state: the one place the tenancy filter
over ListCellState is written, so no reader restates it. The writer is
cell_truth; reads keyed by fill run rather than by sheet stay with the
fill service (a run id is not a tenancy axis).

Every reader yields tuples in the shape its one consumer needs, and
carries the filter that consumer would otherwise write by hand."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime

from django.db import models
from django.db.models import QuerySet

from openbower_schema.fills import SETTLED_CELL_STATES

from ..constants import StoredCellState
from ..models import ListCellState


class CellStateService:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def _scoped(
        self,
        list_id: str,
        *,
        row_ids: Iterable[str] | None = None,
        column_keys: Iterable[str] | None = None,
    ) -> QuerySet[ListCellState]:
        scoped = ListCellState.objects.filter(account_id=self.account_id, list_id=list_id)
        if row_ids is not None:
            scoped = scoped.filter(row_id__in=list(row_ids))
        if column_keys is not None:
            scoped = scoped.filter(column_key__in=list(column_keys))
        return scoped

    def iter_states(
        self, list_id: str, *, row_id: str, column_keys: Iterable[str]
    ) -> Iterator[tuple[str, str, datetime]]:
        """(column key, state, updated at) for one row's records on some
        columns. Raw: a clean filled record is present here, unlike the
        wire reader, which drops it. Ordered by key, so a body built
        from it is byte-stable between two identical sends."""
        yield from (
            self._scoped(list_id, row_ids=[row_id], column_keys=column_keys)
            .order_by("column_key")
            .values_list("column_key", "state", "updated_at")
        )

    def iter_records(
        self, list_id: str, *, row_ids: Iterable[str], column_keys: Iterable[str]
    ) -> Iterator[tuple[str, str, str, datetime]]:
        """(row id, column key, state, updated at) for a page of rows on
        some columns, raw: what the completion judgement (a webhook
        backfill, the flush's re-check) groups per row."""
        yield from (
            self._scoped(list_id, row_ids=row_ids, column_keys=column_keys).values_list(
                "row_id", "column_key", "state", "updated_at"
            )
        )

    def iter_recorded(
        self, list_id: str, *, row_ids: Iterable[str], column_keys: Iterable[str]
    ) -> Iterator[tuple[str, str, str, dict]]:
        """(row id, column key, state, tools) for a page of rows, minus
        the pre-tools clean filled records the wire never shows. DB-side
        narrowing only: a clean run records {"web_search": "open"},
        never {}, so the consumer's Python guard stays the rule."""
        yield from (
            self._scoped(list_id, row_ids=row_ids, column_keys=column_keys)
            .exclude(state=StoredCellState.FILLED, tools={})
            .values_list("row_id", "column_key", "state", "tools")
        )

    def iter_counts_by_column(self, list_id: str, *, column_keys: Iterable[str]) -> Iterator[tuple[str, str, int]]:
        """(column key, state, count) across the whole sheet."""
        yield from (
            self._scoped(list_id, column_keys=column_keys)
            .values_list("column_key", "state")
            .annotate(n=models.Count("id"))
        )

    def iter_settled(
        self, list_id: str, *, row_ids: Iterable[str], column_keys: Iterable[str], fingerprint: str
    ) -> Iterator[tuple[str, str]]:
        """(row id, column key) of every cell that is settled: filled by
        any run, or a settled blank under THIS config (a blank under an
        older config is retryable, not settled)."""
        settled = models.Q(state=StoredCellState.FILLED) | models.Q(
            state__in=SETTLED_CELL_STATES, config_fingerprint=fingerprint
        )
        yield from (
            self._scoped(list_id, row_ids=row_ids, column_keys=column_keys)
            .filter(settled)
            .values_list("row_id", "column_key")
        )

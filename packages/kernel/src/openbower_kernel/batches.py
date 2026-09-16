"""Paged writes over id-ordered rows: the one loop a prune or a purge
runs, so a backlog never builds one unbounded IN list."""

from __future__ import annotations

from collections.abc import Iterator

from django.db.models import QuerySet


def iter_id_pages(queryset: QuerySet, *, batch: int) -> Iterator[list[str]]:
    """Pages of ids, in ULID order, for a write that CONSUMES each page.

    Re-queries after every page instead of cursoring: the page is what
    bounds memory (the ids are never read in full), a consumed page is
    never seen again, and the loop ends when a query comes back empty.
    The caller must move every id it is handed out of the queryset
    before asking for the next page (delete it, or change what the
    filter matches); a page that comes back starting where the last one
    did is a caller that did not, and raises rather than spinning."""
    previous: str | None = None
    while True:
        page = [str(pk) for pk in queryset.order_by("id").values_list("id", flat=True)[:batch]]
        if not page:
            return
        if page[0] == previous:
            raise RuntimeError("iter_id_pages: a page was handed back unconsumed; the caller must remove each page")
        previous = page[0]
        yield page

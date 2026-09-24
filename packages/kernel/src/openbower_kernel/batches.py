"""Paged writes over id-ordered rows: the one loop a prune or a purge
runs, so a backlog never builds one unbounded IN list."""

from __future__ import annotations

from collections.abc import Iterator

from django.db.models import QuerySet


def iter_id_pages(queryset: QuerySet, *, batch: int) -> Iterator[list[str]]:
    """Pages of ids, in ULID order, for a write the caller makes a page
    at a time: a prune, a purge, a column's values stripped from every
    row of a sheet.

    KEYSET on the id, so the LOOP owns its progress: the page bounds
    memory (the ids are never read in full), the cursor advances
    whatever the caller does with a page, and the walk ends on a page
    that comes back empty. A caller that deletes its page and one that
    only rewrites it both terminate, so there is no obligation to get
    wrong.

    What it does not do is re-see a row that enters the filter BEHIND
    the cursor, which needs eligibility to be something other than the
    id. A walk that needs those runs this one again until it yields
    nothing."""
    after = ""
    while True:
        page = [str(pk) for pk in queryset.filter(id__gt=after).order_by("id").values_list("id", flat=True)[:batch]]
        if not page:
            return
        yield page
        after = page[-1]

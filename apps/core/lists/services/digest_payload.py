"""The digest payload, built from rows the sheet holds: pure functions
over a row, its cells, and its cell states, shared by the Send webhook
column's Test and, later, the scheduled flush. No ORM here: callers
read, this shapes."""

from __future__ import annotations

from datetime import datetime

from openbower_schema.webhooks import WebhookDigestData, WebhookDigestItem, WebhookSheetRef

from ..models import List, ListRow


def completion_of(settled_at: dict[str, datetime], wait_keys: list[str]) -> datetime | None:
    """When a row completed for a set of waited-on columns: the newest
    settle among them once EVERY one has a cell-state record, else None
    (absence means never attempted, so the row is not complete)."""
    if any(key not in settled_at for key in wait_keys):
        return None
    return max(settled_at[key] for key in wait_keys)


def build_digest_item(
    *,
    scope: str,
    row: ListRow,
    cells: dict[str, str],
    states: dict[str, str],
    completed_at: datetime | None,
    sent_at: datetime,
) -> WebhookDigestItem:
    """One item. The key is `<scope>:<row>:<time>`: the scope is what
    the digest is FOR (a sheet for a test send; the webhook node once
    one exists), so two digests of one sheet never share keys; the
    time is the completion, or the send time for a sample that has
    not completed. Opaque to receivers, who dedup on it whole."""
    stamp = (completed_at or sent_at).isoformat()
    return WebhookDigestItem(
        key=f"{scope}:{row.id}:{stamp}",
        row_id=str(row.id),
        position=row.position,
        completed_at=completed_at.isoformat() if completed_at else None,
        cells=cells,
        states=states,
    )


def build_digest_data(
    target_list: List, *, column_keys: list[str], items: list[WebhookDigestItem]
) -> WebhookDigestData:
    return WebhookDigestData(
        sheet=WebhookSheetRef(id=str(target_list.id), label=target_list.label),
        column_keys=list(column_keys),
        items=items,
    )

"""The Send webhook column, before it exists as a column: its Test
send. Reads the sheet (columns, one row, that row's cell states),
shapes a digest of one sample item, and hands it to the destination's
delivery funnel. The column itself, its path and nodes, arrive with
the next phase; this is what a user sees first."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from webhooks.models import WebhookDelivery
from webhooks.services import DestinationNotFound, WebhookDestinationService

from ..constants import WebhookColumnErrorCode
from ..models import ListCellState, ListRow
from .digest_payload import build_digest_data, build_digest_item, completion_of
from .lists import ListService, cells_for_storage


class WebhookColumnRefused(Exception):
    """Base for refusals: `code` is the machine leg the view maps to a
    status, str(self) is server-authored copy the client renders
    verbatim."""

    code: WebhookColumnErrorCode


class WebhookColumnUnknown(WebhookColumnRefused):
    code = WebhookColumnErrorCode.COLUMN_UNKNOWN

    def __init__(self, key: str) -> None:
        super().__init__(f"No column with key {key}.")


class WebhookColumnNotAi(WebhookColumnRefused):
    code = WebhookColumnErrorCode.COLUMN_NOT_AI

    def __init__(self, label: str) -> None:
        super().__init__(f"{label} is not an AI column; a webhook waits on AI columns only.")


class WebhookRowUnknown(WebhookColumnRefused):
    code = WebhookColumnErrorCode.ROW_UNKNOWN

    def __init__(self) -> None:
        super().__init__("That row is no longer on the sheet; reload and try again.")


class WebhookDestinationUnknown(WebhookColumnRefused):
    code = WebhookColumnErrorCode.DESTINATION_UNKNOWN

    def __init__(self) -> None:
        super().__init__("That destination no longer exists; refresh the list.")


class WebhookColumnService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.lists = ListService(account_id=account_id)
        self.destinations = WebhookDestinationService(account_id=account_id, user_id=user_id)

    def test(
        self,
        target_list_id: str,
        *,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        row_id: str,
        cells: dict[str, str],
    ) -> WebhookDelivery:
        """One sample digest to a destination: the given row, the
        caller's (possibly edited) values for the payload columns, and
        the row's stored states for the waited-on columns, sent as a
        test. Raises ListNotFound for the sheet (the URL's reference);
        everything named in the body refuses with a code."""
        target_list = self.lists.get(target_list_id)
        by_key = {column["key"]: column for column in target_list.columns}
        for key in wait_keys:
            column = by_key.get(key)
            if column is None:
                raise WebhookColumnUnknown(key)
            if not column.get("fill"):
                raise WebhookColumnNotAi(column["label"])
        for key in payload_keys:
            if key not in by_key:
                raise WebhookColumnUnknown(key)
        row = ListRow.objects.filter(list_id=str(target_list.id), id=row_id).first()
        if row is None:
            raise WebhookRowUnknown()
        try:
            destination = self.destinations.get(destination_id)
        except DestinationNotFound as e:
            raise WebhookDestinationUnknown() from e

        # The sample carries exactly what the sheet would hold: the
        # values normalized and clamped by the one cell transform.
        types = {key: by_key[key]["type"] for key in payload_keys}
        stored, _mismatches = cells_for_storage(types, cells, where="webhook_test")
        settled_at: dict[str, datetime] = {}
        states: dict[str, str] = {}
        # Raw states, not the wire reader (which drops a clean filled),
        # scoped by account like every cell-state read.
        rows = ListCellState.objects.filter(
            account_id=self.account_id,
            list_id=str(target_list.id),
            row_id=str(row.id),
            column_key__in=wait_keys,
        ).values_list("column_key", "state", "updated_at")
        for key, state, updated_at in rows:
            states[key] = state
            settled_at[key] = updated_at
        sent_at = timezone.now()
        item = build_digest_item(
            scope=str(target_list.id),
            row=row,
            cells=stored,
            states=states,
            completed_at=completion_of(settled_at, wait_keys),
            sent_at=sent_at,
        )
        data = build_digest_data(target_list, column_keys=wait_keys, items=[item])
        return self.destinations.deliver(destination, test=True, data=data)

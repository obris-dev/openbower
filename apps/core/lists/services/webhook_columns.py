"""The Send webhook column: what it is on the sheet (a column entry
pointing at the webhook node at rank 1 of its own path, behind a wait
node naming the paths it waits on), how it is added, read back, and
changed, and its Test and Preview sends. The substrate persists the
nodes it is handed (WorkflowService.create_path); this module knows
what a webhook column's path looks like. Account-scoped like every
lists service."""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from openbower_schema.lists import DEFAULT_COLUMN_TYPE, AiColumn, WebhookCellState, WebhookColumn
from openbower_schema.webhooks import WebhookColumnConfigWire, WebhookDigestData, WebhookEnvelope
from webhooks.models import WebhookDestination
from webhooks.services import DestinationNotFound, Sent, WebhookDestinationService, envelope_of

from ..constants import WebhookColumnErrorCode
from ..models import List, ListRow, Node
from ..nodes.wait_until import WaitUntil
from ..nodes.webhook import Webhook
from .cell_states import CellStateService
from .columns import claim_key, locked_list
from .digest_payload import build_digest_data, build_digest_item, completion_of
from .lists import ListService, cells_for_storage
from .webhook_paths import inbound_paths_for, wait_keys_for
from .workflows import WorkflowService, config_as


class WebhookColumnRefused(Exception):
    """Base for refusals: `code` is the machine leg the view maps to a
    status, str(self) is server-authored copy the client renders
    verbatim."""

    code = WebhookColumnErrorCode.WEBHOOK_COLUMN_REFUSED


class WebhookColumnUnknown(WebhookColumnRefused):
    code = WebhookColumnErrorCode.COLUMN_UNKNOWN

    def __init__(self, key: str) -> None:
        super().__init__(f"No column with key {key}.")


class WebhookColumnNotAi(WebhookColumnRefused):
    code = WebhookColumnErrorCode.COLUMN_NOT_AI

    def __init__(self, label: str) -> None:
        super().__init__(f"{label} is not an AI column; a webhook waits on AI columns only.")


class WebhookColumnNotWebhook(WebhookColumnRefused):
    code = WebhookColumnErrorCode.COLUMN_NOT_WEBHOOK

    def __init__(self, label: str) -> None:
        super().__init__(f"{label} is not a Send webhook column.")


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
        self.cell_states = CellStateService(account_id=account_id)
        self.destinations = WebhookDestinationService(account_id=account_id, user_id=user_id)
        self.workflows = WorkflowService(account_id=account_id)

    # The two reads other custody asks of this column's nodes.

    def waits_naming(self, path_id: str) -> QuerySet[Node]:
        """The wait nodes whose inbound set names a path."""
        return self.workflows.nodes_of_kind(WaitUntil.KIND, config__inbound_path_ids__contains=[path_id])

    def webhooks_for_destination(self, destination_id: str) -> QuerySet[Node]:
        return self.workflows.nodes_of_kind(Webhook.KIND, config__destination_id=destination_id)

    # Custody.

    def add(
        self,
        target_list_id: str,
        *,
        label: str,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        interval_seconds: int,
    ) -> List:
        """The column, its path, and its two nodes, in one transaction
        under the list lock (the same lock every columns writer takes)."""
        with transaction.atomic():
            target_list = locked_list(self.account_id, target_list_id)
            key = claim_key(target_list, label=label)
            self._validate(target_list, wait_keys=wait_keys, payload_keys=payload_keys, destination_id=destination_id)
            wait = WaitUntil(inbound_path_ids=self._inbound_paths(target_list, wait_keys))
            webhook = Webhook(
                destination_id=destination_id, interval_seconds=interval_seconds, payload_keys=payload_keys
            )
            _path, nodes = self.workflows.create_path(target_list, [wait, webhook])
            column = WebhookColumn(key=key, label=label, type=DEFAULT_COLUMN_TYPE, node_id=str(nodes[1].id))
            target_list.columns = [*target_list.columns, column]
            target_list.save(update_fields=["columns", "updated_at"])
        return target_list

    def cell_states_for_rows(self, target_list: List, rows: list[ListRow]) -> dict[str, dict[str, WebhookCellState]]:
        """row id -> {webhook column key: state} for one page of rows,
        the webhook columns' half of what the AI states are to theirs.
        A row is WAITING for a webhook column once every column it
        waits on is done for that row, by the digest's own completion
        rule, so the cell and the payload can never disagree about
        which rows are due; a row still filling or never attempted has
        no entry and shows nothing. Nothing is sent before the flush,
        so waiting is the one word today; sent and failed join it then.
        One records read over the union of the waited-on columns."""
        webhook_columns = [column for column in target_list.columns if isinstance(column, WebhookColumn)]
        if not webhook_columns or not rows:
            return {}
        wait_keys_by_column: dict[str, list[str]] = {}
        for column in webhook_columns:
            webhook_node = self.workflows.get_node(column.node_id)
            wait_keys_by_column[column.key] = self._wait_keys(target_list, webhook_node)
        watched = {key for keys in wait_keys_by_column.values() for key in keys}
        row_ids = [str(row.id) for row in rows]
        records: dict[str, dict[str, tuple[str, datetime]]] = {}
        recorded = self.cell_states.iter_records(str(target_list.id), row_ids=row_ids, column_keys=watched)
        for row_id, column_key, state, updated_at in recorded:
            records.setdefault(row_id, {})[column_key] = (state, updated_at)
        states: dict[str, dict[str, WebhookCellState]] = {}
        for row_id, row_records in records.items():
            for key, wait_keys in wait_keys_by_column.items():
                if wait_keys and completion_of(row_records, wait_keys) is not None:
                    states.setdefault(row_id, {})[key] = "waiting"
        return states

    def config(self, target_list_id: str, key: str) -> WebhookColumnConfigWire:
        target_list = self.lists.get(target_list_id)
        webhook_node = self._webhook_node(target_list, key)
        return self._wire(target_list, webhook_node)

    def update(
        self,
        target_list_id: str,
        key: str,
        *,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        interval_seconds: int,
        enabled: bool,
    ) -> WebhookColumnConfigWire:
        """Both node configs rewritten in one transaction; the path keeps
        its shape."""
        with transaction.atomic():
            target_list = locked_list(self.account_id, target_list_id)
            webhook_node = self._webhook_node(target_list, key)
            self._validate(target_list, wait_keys=wait_keys, payload_keys=payload_keys, destination_id=destination_id)
            wait = WaitUntil(inbound_path_ids=self._inbound_paths(target_list, wait_keys))
            webhook = Webhook(
                destination_id=destination_id,
                interval_seconds=interval_seconds,
                payload_keys=payload_keys,
                enabled=enabled,
            )
            _wait_node, webhook_node = self.workflows.replace_path_nodes(webhook_node.path_id, [wait, webhook])
        return self._wire(target_list, webhook_node)

    # The sends.

    def test(
        self,
        target_list_id: str,
        *,
        key: str,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        row_id: str,
        cells: dict[str, str],
    ) -> Sent:
        """One sample digest to a destination, sent as a test and
        recorded. Raises ListNotFound for the sheet (the URL's
        reference); everything named in the body refuses with a code."""
        destination, data = self._sample(
            target_list_id,
            key=key,
            destination_id=destination_id,
            wait_keys=wait_keys,
            payload_keys=payload_keys,
            row_id=row_id,
            cells=cells,
        )
        return self.destinations.deliver(destination, test=True, data=data)

    def preview(
        self,
        target_list_id: str,
        *,
        key: str,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        row_id: str,
        cells: dict[str, str],
    ) -> WebhookEnvelope:
        """The envelope a test send would carry: rendered, sent nowhere,
        recorded nowhere."""
        _destination, data = self._sample(
            target_list_id,
            key=key,
            destination_id=destination_id,
            wait_keys=wait_keys,
            payload_keys=payload_keys,
            row_id=row_id,
            cells=cells,
        )
        return envelope_of(test=True, data=data)

    def _sample(
        self,
        target_list_id: str,
        *,
        key: str,
        destination_id: str,
        wait_keys: list[str],
        payload_keys: list[str],
        row_id: str,
        cells: dict[str, str],
    ) -> tuple[WebhookDestination, WebhookDigestData]:
        """The given row, the caller's (possibly edited) values for the
        payload columns, and the row's stored states for the waited-on
        columns, as a digest of one item. The item's scope is the column's
        webhook node when the column exists (`key`), else the sheet."""
        target_list = self.lists.get(target_list_id)
        by_key = {column.key: column for column in target_list.columns}
        scope = str(target_list.id)
        if key:
            webhook_node = self._webhook_node(target_list, key)
            scope = str(webhook_node.id)
        destination = self._validate(
            target_list, wait_keys=wait_keys, payload_keys=payload_keys, destination_id=destination_id
        )
        row = ListRow.objects.filter(list_id=str(target_list.id), id=row_id).first()
        if row is None:
            raise WebhookRowUnknown()

        # The sample carries exactly what the sheet would hold: the
        # values normalized and clamped by the one cell transform.
        types = {key: by_key[key].type for key in payload_keys}
        stored, _mismatches = cells_for_storage(types, cells, where="webhook_test")
        # Raw states, not the wire reader (which drops a clean filled).
        rows = self.cell_states.iter_states(str(target_list.id), row_id=str(row.id), column_keys=wait_keys)
        records: dict[str, tuple[str, datetime]] = {k: (state, updated_at) for k, state, updated_at in rows}
        states = {k: state for k, (state, _updated_at) in records.items()}
        item = build_digest_item(
            scope=scope,
            row=row,
            cells=stored,
            states=states,
            completed_at=completion_of(records, wait_keys),
            sent_at=timezone.now(),
            test=True,
        )
        return destination, build_digest_data(target_list, waited_on=wait_keys, items=[item])

    # Shared pieces.

    def _validate(
        self, target_list: List, *, wait_keys: list[str], payload_keys: list[str], destination_id: str
    ) -> WebhookDestination:
        """Every body reference resolves: wait keys are AI columns,
        payload keys are columns, the destination is this account's."""
        by_key = {column.key: column for column in target_list.columns}
        for key in wait_keys:
            column = by_key.get(key)
            if column is None:
                raise WebhookColumnUnknown(key)
            if not isinstance(column, AiColumn):
                raise WebhookColumnNotAi(column.label)
        for key in payload_keys:
            if key not in by_key:
                raise WebhookColumnUnknown(key)
        try:
            return self.destinations.get(destination_id)
        except DestinationNotFound as e:
            raise WebhookDestinationUnknown() from e

    def _webhook_node(self, target_list: List, key: str) -> Node:
        column = next((column for column in target_list.columns if column.key == key), None)
        if column is None:
            raise WebhookColumnUnknown(key)
        if not isinstance(column, WebhookColumn):
            raise WebhookColumnNotWebhook(column.label)
        return self.workflows.get_node(column.node_id)

    def _inbound_paths(self, target_list: List, wait_keys: list[str]) -> list[str]:
        node_ids = [column.node_id for column in target_list.columns if isinstance(column, AiColumn)]
        agent_nodes = Node.objects.filter(account_id=self.account_id, id__in=node_ids)
        path_by_node = {str(node.id): node.path_id for node in agent_nodes}
        return inbound_paths_for(wait_keys, columns=target_list.columns, path_by_node=path_by_node)

    def _wait_keys(self, target_list: List, webhook_node: Node) -> list[str]:
        """The columns a webhook column waits on, in sheet order: its
        wait node's inbound paths resolved to the columns they fill."""
        wait_node, _webhook = self.workflows.nodes_on_path(webhook_node.path_id)
        wait = config_as(wait_node, WaitUntil)
        agent_nodes = Node.objects.filter(account_id=self.account_id, path_id__in=wait.inbound_path_ids)
        node_by_path = {node.path_id: str(node.id) for node in agent_nodes}
        return wait_keys_for(wait.inbound_path_ids, columns=target_list.columns, node_by_path=node_by_path)

    def _wire(self, target_list: List, webhook_node: Node) -> WebhookColumnConfigWire:
        webhook = config_as(webhook_node, Webhook)
        try:
            destination_label = self.destinations.get(webhook.destination_id).label
        except DestinationNotFound as e:
            raise WebhookDestinationUnknown() from e
        return WebhookColumnConfigWire(
            node_id=str(webhook_node.id),
            destination_id=webhook.destination_id,
            destination_label=destination_label,
            wait_keys=self._wait_keys(target_list, webhook_node),
            payload_keys=webhook.payload_keys,
            interval_seconds=webhook.interval_seconds,
            enabled=webhook.enabled,
        )

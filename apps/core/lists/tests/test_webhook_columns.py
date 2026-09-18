"""The Send webhook column as custody: added with its path and two
nodes, read back, rewritten, deleted with its nodes; the refusals that
protect it (an AI column it waits on cannot go first) and the readers
that keep it off the ingest surface. Session auth is real; the sender
is patched at the destination service's seam for the scoped test send.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from lists.constants import CellSource, FillErrorCode, StoredCellState, WebhookColumnErrorCode
from lists.models import Node, NodePath
from lists.nodes.wait_until import WaitUntil
from lists.nodes.webhook import Webhook
from lists.services import cell_truth
from lists.services.digest_payload import event_id_of
from lists.services.lists import ListService
from lists.services.workflows import WorkflowService, config_as
from openbower_schema.lists import ListSummary
from openbower_schema.webhooks import WebhookColumnConfigWire, WebhookEnvelope
from webhooks.constants import DeliveryStatus
from webhooks.delivery.protocol import DeliveryResult
from webhooks.services import WebhookDestinationService

AGENT = "01AGT" + "A" * 21
OTHER_AGENT = "01AGT" + "B" * 21


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, **kwargs) -> DeliveryResult:
        self.calls.append(kwargs)
        return DeliveryResult(DeliveryStatus.OK, 200, "", 9, "")


class WebhookColumnTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.account_id = TEST_IDENTITY["account_id"]
        self.lists = ListService(account_id=self.account_id)
        self.workflows = WorkflowService(account_id=self.account_id)
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"],
            label="Prospects",
            columns=[{"kind": "plain", "key": "company", "label": "Company", "type": "text"}],
            origin="manual",
        )
        # Two real AI nodes on their own paths: one agent with two
        # outputs, and a second agent.
        first = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=AGENT)
        second = self.workflows.get_or_create_column_agent_node(self.sheet, agent_id=OTHER_AGENT)
        self.sheet.columns = [
            *self.sheet.columns,
            {"key": "answer", "label": "Answer", "type": "text", "kind": "ai", "node_id": str(first.id)},
            {"key": "score", "label": "Score", "type": "text", "kind": "ai", "node_id": str(first.id)},
            {"key": "country", "label": "Country", "type": "text", "kind": "ai", "node_id": str(second.id)},
        ]
        self.sheet.save(update_fields=["columns", "updated_at"])
        self.first_path, self.second_path = first.path_id, second.path_id
        self.rows = self.lists.add_rows(self.sheet, [{"company": "acme.com"}])
        destinations = WebhookDestinationService(account_id=self.account_id, user_id=TEST_IDENTITY["id"])
        self.destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})
        self.baseline = (Node.objects.count(), NodePath.objects.count())

    def _body(self, **overrides) -> dict:
        return {
            "label": "CRM sync",
            "destination_id": str(self.destination.id),
            "wait_keys": ["country", "answer"],
            "payload_keys": ["company", "country"],
            "interval_seconds": 3600,
            **overrides,
        }

    def _add(self, **overrides):
        return self.client.post(
            reverse("lists_columns_webhook", kwargs={"id": str(self.sheet.id)}),
            self._body(**overrides),
            content_type="application/json",
        )

    def _config_url(self, key: str = "crm_sync") -> str:
        return reverse("lists_column_webhook", kwargs={"id": str(self.sheet.id), "key": key})

    def test_add_persists_the_column_with_its_path_and_two_nodes(self):
        resp = self._add()
        self.assertEqual(resp.status_code, 201, resp.content)
        summary = ListSummary(**resp.json())
        column = next(c for c in summary.columns if c.key == "crm_sync")
        self.assertEqual((column.type, column.kind), ("text", "webhook"))
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), (self.baseline[0] + 2, self.baseline[1] + 1))
        webhook_node = self.workflows.get_node(column.node_id)
        wait_node, same = self.workflows.nodes_on_path(webhook_node.path_id)
        self.assertEqual((wait_node.rank, same.id), (0, webhook_node.id))
        # Two wait keys of two agents: two inbound paths, in the order given.
        self.assertEqual(config_as(wait_node, WaitUntil).inbound_path_ids, [self.second_path, self.first_path])
        webhook = config_as(webhook_node, Webhook)
        self.assertEqual(
            (webhook.destination_id, webhook.interval_seconds, webhook.payload_keys, webhook.enabled),
            (
                str(self.destination.id),
                3600,
                ["company", "country"],
                True,
            ),
        )

    def test_the_config_derives_every_column_the_waited_paths_fill_in_sheet_order(self):
        self._add(wait_keys=["country", "answer"])
        resp = self.client.get(self._config_url())
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = WebhookColumnConfigWire(**resp.json())
        # Waiting on answer's path is waiting on score too (one agent).
        self.assertEqual(wire.wait_keys, ["answer", "score", "country"])
        self.assertEqual((wire.destination_label, wire.interval_seconds, wire.enabled), ("CRM", 3600, True))

    def test_add_refusals_leave_nothing_behind(self):
        cases = [
            ({"wait_keys": ["nope"]}, 400, WebhookColumnErrorCode.COLUMN_UNKNOWN),
            ({"wait_keys": ["company"]}, 400, WebhookColumnErrorCode.COLUMN_NOT_AI),
            ({"payload_keys": ["nope"]}, 400, WebhookColumnErrorCode.COLUMN_UNKNOWN),
            ({"destination_id": "01DST" + "Z" * 21}, 400, WebhookColumnErrorCode.DESTINATION_UNKNOWN),
            ({"label": "Company"}, 400, FillErrorCode.COLUMN_EXISTS),
            ({"label": "!!!"}, 400, FillErrorCode.RESERVED_KEY),
        ]
        for overrides, status, code in cases:
            with self.subTest(code=code):
                resp = self._add(**overrides)
                self.assertEqual(resp.status_code, status, resp.content)
                self.assertEqual(resp.json()["error"], code)
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), self.baseline)
        self.assertEqual(len(ListService(account_id=self.account_id).get(str(self.sheet.id)).columns), 4)

    def test_an_interval_outside_the_presets_is_a_shape_400(self):
        resp = self._add(interval_seconds=1234)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), self.baseline)

    def test_patch_rewrites_both_configs_in_place(self):
        self._add()
        before = {n.id for n in Node.objects.all()}
        resp = self.client.patch(
            self._config_url(),
            {
                "destination_id": str(self.destination.id),
                "wait_keys": ["country"],
                "payload_keys": ["company"],
                "interval_seconds": 300,
                "enabled": False,
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        wire = WebhookColumnConfigWire(**resp.json())
        self.assertEqual(
            (wire.wait_keys, wire.payload_keys, wire.interval_seconds, wire.enabled),
            (["country"], ["company"], 300, False),
        )
        self.assertEqual({n.id for n in Node.objects.all()}, before)

    def test_reorder_and_rename_carry_the_webhook_member(self):
        self._add()
        resp = self.client.patch(
            reverse("lists_columns_order", kwargs={"id": str(self.sheet.id)}),
            {"keys": ["crm_sync", "company", "answer", "score", "country"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        first = ListSummary(**resp.json()).columns[0]
        self.assertEqual(first.key, "crm_sync")
        self.assertEqual(first.kind, "webhook")

    def test_a_scoped_test_send_uses_the_webhook_node_as_the_event_scope(self):
        self._add()
        column = next(c for c in self.lists.get(str(self.sheet.id)).columns if c.key == "crm_sync")
        # Settle the wait key so the item's stamp is its completion, a
        # value the test can read back, rather than the send clock.
        cell_truth.write(
            account_id=self.account_id,
            list_id=str(self.sheet.id),
            row_id=str(self.rows[0].id),
            fill_run_id=None,
            config_fingerprint="",
            states={"country": StoredCellState.FILLED},
            tools={},
            source=CellSource.FILL,
        )
        fake = _FakeSender()
        body = {
            "key": "crm_sync",
            "destination_id": str(self.destination.id),
            "wait_keys": ["country"],
            "payload_keys": ["company"],
            "row_id": str(self.rows[0].id),
            "cells": {"company": "acme.com"},
        }
        with patch("webhooks.services.destinations.WebhookSender", return_value=fake):
            resp = self.client.post(
                reverse("lists_columns_webhook_test", kwargs={"id": str(self.sheet.id)}),
                body,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        [item] = WebhookEnvelope.model_validate_json(fake.calls[0]["body"]).data.items
        self.assertIsNotNone(item.completed_at)
        row_id = str(self.rows[0].id)
        scoped = event_id_of(scope=column.node_id, row_id=row_id, stamp=item.completed_at, test=True)
        self.assertEqual(item.event_id, scoped)
        # And NOT the sheet-scoped id the add drawer's test send carries.
        unscoped = event_id_of(scope=str(self.sheet.id), row_id=row_id, stamp=item.completed_at, test=True)
        self.assertNotEqual(item.event_id, unscoped)

    def _settle(self, row_id: str, states: dict[str, StoredCellState]) -> None:
        cell_truth.write(
            account_id=self.account_id,
            list_id=str(self.sheet.id),
            row_id=row_id,
            fill_run_id=None,
            config_fingerprint="",
            states=states,
            tools={},
            source=CellSource.FILL,
        )

    def test_rows_read_waiting_only_once_every_waited_on_column_is_done(self):
        """The cell tracks the digest's completion rule: an answer or a
        settled blank on EVERY waited-on column, and nothing before.
        Waiting on `answer` waits on its PATH, so `score` (the same
        agent's other output) counts too."""
        self._add(wait_keys=["country", "answer"])
        filling, done, blank, fresh = (
            str(row.id)
            for row in [
                *self.rows,
                *self.lists.add_rows(self.sheet, [{"company": "b.com"}, {"company": "c.com"}, {"company": "d.com"}]),
            ]
        )
        filled = StoredCellState.FILLED
        # Two of three done: the retryable third keeps the row filling.
        self._settle(filling, {"country": filled, "answer": filled, "score": StoredCellState.TRANSIENT})
        self._settle(done, {"country": filled, "answer": filled, "score": filled})
        self._settle(blank, {"country": StoredCellState.NO_EVIDENCE, "answer": filled, "score": filled})
        resp = self.client.get(reverse("lists_rows", kwargs={"id": str(self.sheet.id)}))
        self.assertEqual(resp.status_code, 200, resp.content)
        by_row = {item["id"]: item["webhooks"] for item in resp.json()["items"]}
        self.assertEqual(
            by_row,
            {
                filling: {},
                done: {"crm_sync": "waiting"},
                blank: {"crm_sync": "waiting"},
                fresh: {},
            },
        )

    def test_deleting_the_webhook_column_removes_its_path_and_nodes_only(self):
        self._add()
        resp = self.client.delete(reverse("lists_column_detail", kwargs={"id": str(self.sheet.id), "key": "crm_sync"}))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual((Node.objects.count(), NodePath.objects.count()), self.baseline)
        self.assertNotIn("crm_sync", [c.key for c in self.lists.get(str(self.sheet.id)).columns])

    def test_deleting_a_waited_on_ai_column_is_refused_until_the_webhook_goes(self):
        self._add(wait_keys=["country"])
        url = reverse("lists_column_detail", kwargs={"id": str(self.sheet.id), "key": "country"})
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], FillErrorCode.COLUMN_WAITED_ON)
        self.assertIn("CRM sync", resp.json()["detail"])
        # A sibling of a waited-on path is load-bearing too; an unrelated AI column is not.
        self.assertEqual(
            self.client.delete(
                reverse("lists_column_detail", kwargs={"id": str(self.sheet.id), "key": "answer"})
            ).status_code,
            200,
        )
        self.client.delete(reverse("lists_column_detail", kwargs={"id": str(self.sheet.id), "key": "crm_sync"}))
        self.assertEqual(self.client.delete(url).status_code, 200)

    def test_the_ingest_surface_never_offers_or_accepts_the_webhook_column(self):
        self._add()
        schema = self.client.get(reverse("lists_ingest", kwargs={"id": str(self.sheet.id)}))
        self.assertEqual(schema.status_code, 200, schema.content)
        self.assertNotIn("crm_sync", [c["key"] for c in schema.json()["columns"]])

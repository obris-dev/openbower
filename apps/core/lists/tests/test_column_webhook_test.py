"""The Send webhook column's Test send: a sample digest of one row to a
destination, with the caller's edited values, the row's stored states,
and the completion predicate; every body refusal with its code.
Session auth is real; the HTTP path is patched at the destination
service's sender seam.

Run: DJANGO_ENV=test uv run python manage.py test lists
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from common.testing import TEST_IDENTITY, login_session
from lists.constants import CELL_MAX_LENGTH, StoredCellState, WebhookColumnErrorCode
from lists.services import cell_truth
from lists.services.lists import ListService
from openbower_schema.webhooks import WebhookDeliveryWire, WebhookDigestData, WebhookEnvelope
from webhooks.constants import DeliveryStatus
from webhooks.delivery.protocol import DeliveryResult
from webhooks.models import WebhookDelivery
from webhooks.services import WebhookDestinationService

NODE = "01ND" + "A" * 22
COLUMNS = [
    {"key": "company", "label": "Company", "type": "text"},
    {"key": "answer", "label": "Answer", "type": "text", "fill": {"node_id": NODE}},
    {"key": "score", "label": "Score", "type": "text", "fill": {"node_id": NODE}},
]


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, **kwargs) -> DeliveryResult:
        self.calls.append(kwargs)
        return DeliveryResult(DeliveryStatus.OK, 200, "", 9, "")


class ColumnWebhookTestTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)
        self.account_id = TEST_IDENTITY["account_id"]
        self.lists = ListService(account_id=self.account_id)
        self.sheet = self.lists.create(
            owner_id=TEST_IDENTITY["id"], label="Prospects", columns=COLUMNS, origin="manual"
        )
        self.rows = self.lists.add_rows(self.sheet, [{"company": "acme.com"}, {"company": "example.io"}])
        destinations = WebhookDestinationService(account_id=self.account_id, user_id=TEST_IDENTITY["id"])
        self.destination, _ = destinations.create(label="CRM", url="https://hooks.example.com/in", headers={})

    def _settle(self, row_id: str, states: dict[str, StoredCellState]) -> None:
        cell_truth.write(
            account_id=self.account_id,
            list_id=str(self.sheet.id),
            row_id=row_id,
            fill_run_id=None,
            config_fingerprint="",
            states=states,
            tools={},
        )

    def _post(self, **overrides):
        body = {
            "destination_id": str(self.destination.id),
            "wait_keys": ["answer", "score"],
            "payload_keys": ["company", "answer"],
            "row_id": str(self.rows[0].id),
            "cells": {"company": "edited.example", "answer": "yes"},
            **overrides,
        }
        fake = _FakeSender()
        with patch("webhooks.services.destinations.WebhookSender", return_value=fake):
            resp = self.client.post(
                reverse("lists_columns_webhook_test", kwargs={"id": str(self.sheet.id)}),
                body,
                content_type="application/json",
            )
        return resp, fake

    def test_sends_a_sample_digest_with_the_edited_cells_and_the_stored_states(self):
        self._settle(str(self.rows[0].id), {"answer": StoredCellState.FILLED})
        resp, fake = self._post()
        self.assertEqual(resp.status_code, 200, resp.content)
        delivery = resp.json()
        WebhookDeliveryWire(**delivery)
        self.assertEqual(delivery["type"], "digest")
        self.assertTrue(delivery["test"])
        self.assertEqual(delivery["destination_id"], str(self.destination.id))

        envelope = WebhookEnvelope.model_validate_json(fake.calls[0]["body"])
        self.assertEqual(envelope.type, "digest")
        self.assertTrue(envelope.test)
        data = envelope.data
        self.assertIsInstance(data, WebhookDigestData)
        self.assertEqual(data.sheet.id, str(self.sheet.id))
        self.assertEqual(data.column_keys, ["answer", "score"])
        [item] = data.items
        self.assertEqual(item.row_id, str(self.rows[0].id))
        self.assertEqual(item.position, 1)
        # The caller's edited value wins over the stored cell; only the
        # payload columns ride.
        self.assertEqual(item.cells, {"company": "edited.example", "answer": "yes"})
        # The settled wait key is present; the unattempted one is absent,
        # so the row is not complete.
        self.assertEqual(item.states, {"answer": StoredCellState.FILLED})
        self.assertIsNone(item.completed_at)
        self.assertTrue(item.key.startswith(f"{self.sheet.id}:{self.rows[0].id}:"))

        row = WebhookDelivery.objects.get(id=delivery["id"])
        self.assertEqual(row.destination_id, str(self.destination.id))
        self.assertTrue(row.test)

    def test_a_row_with_every_wait_key_settled_is_complete(self):
        self._settle(str(self.rows[0].id), {"answer": StoredCellState.FILLED, "score": StoredCellState.NO_EVIDENCE})
        resp, fake = self._post()
        self.assertEqual(resp.status_code, 200)
        [item] = WebhookEnvelope.model_validate_json(fake.calls[0]["body"]).data.items
        self.assertIsNotNone(item.completed_at)
        self.assertEqual(item.key, f"{self.sheet.id}:{self.rows[0].id}:{item.completed_at}")
        self.assertEqual(set(item.states), {"answer", "score"})

    def test_a_long_cell_arrives_clamped(self):
        resp, fake = self._post(cells={"company": "x" * (CELL_MAX_LENGTH + 10), "answer": ""})
        self.assertEqual(resp.status_code, 200)
        [item] = WebhookEnvelope.model_validate_json(fake.calls[0]["body"]).data.items
        self.assertEqual(len(item.cells["company"]), CELL_MAX_LENGTH)

    def test_refusals_carry_their_codes(self):
        cases = [
            ({"wait_keys": ["nope"]}, WebhookColumnErrorCode.COLUMN_UNKNOWN),
            ({"wait_keys": ["company"]}, WebhookColumnErrorCode.COLUMN_NOT_AI),
            ({"payload_keys": ["nope"], "cells": {"nope": "x"}}, WebhookColumnErrorCode.COLUMN_UNKNOWN),
            ({"row_id": "01ROW" + "Z" * 21}, WebhookColumnErrorCode.ROW_UNKNOWN),
            ({"destination_id": "01DS" + "Z" * 22}, WebhookColumnErrorCode.DESTINATION_UNKNOWN),
        ]
        for overrides, code in cases:
            with self.subTest(code=code):
                resp, fake = self._post(**overrides)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"], code)
                self.assertEqual(fake.calls, [])
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    def test_a_row_of_another_sheet_reads_as_unknown(self):
        other = self.lists.create(owner_id=TEST_IDENTITY["id"], label="Other", columns=COLUMNS, origin="manual")
        [foreign] = self.lists.add_rows(other, [{"company": "else.example"}])
        resp, _ = self._post(row_id=str(foreign.id))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], WebhookColumnErrorCode.ROW_UNKNOWN)

    def test_shape_refusals(self):
        # Cells must be exactly the payload keys; keys must be unique.
        resp, _ = self._post(cells={"company": "x"})
        self.assertEqual(resp.status_code, 400)
        resp, _ = self._post(cells={"company": "x", "answer": "y", "score": "z"})
        self.assertEqual(resp.status_code, 400)
        resp, _ = self._post(wait_keys=["answer", "answer"])
        self.assertEqual(resp.status_code, 400)

    def test_unknown_list_is_404_and_no_cookie_is_401(self):
        resp = self.client.post(
            reverse("lists_columns_webhook_test", kwargs={"id": "01LS" + "Z" * 22}),
            {"destination_id": "x", "wait_keys": ["a"], "payload_keys": ["a"], "row_id": "r", "cells": {"a": ""}},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.client.cookies.clear()
        resp, _ = self._post()
        self.assertEqual(resp.status_code, 401)

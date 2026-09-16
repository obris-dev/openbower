"""The delivery funnel: the envelope's type comes from its data, the
test flag from the caller, and both land on the log row exactly as
sent; a mismatched type/data pair cannot be built.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from pydantic import ValidationError

from common.testing import TEST_IDENTITY
from openbower_schema.webhooks import (
    WebhookDigestData,
    WebhookDigestItem,
    WebhookEnvelope,
    WebhookPingData,
    WebhookSheetRef,
)
from webhooks.constants import DeliveryStatus, WebhookEnvelopeType
from webhooks.delivery.protocol import DeliveryResult
from webhooks.services import WebhookDestinationService


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, **kwargs) -> DeliveryResult:
        self.calls.append(kwargs)
        return DeliveryResult(DeliveryStatus.OK, 200, "", 3, "")


def _digest() -> WebhookDigestData:
    return WebhookDigestData(
        sheet=WebhookSheetRef(id="01LIST" + "A" * 20, label="Prospects"),
        column_keys=["answer"],
        items=[
            WebhookDigestItem(
                key="k",
                row_id="01ROW" + "A" * 21,
                position=1,
                completed_at=None,
                cells={"company": "acme.com"},
                states={"answer": "filled"},
            )
        ],
    )


class DeliverTests(TestCase):
    def setUp(self) -> None:
        self.service = WebhookDestinationService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])
        self.destination, _ = self.service.create(label="CRM", url="https://hooks.example.com/in", headers={})

    def _deliver(self, *, test: bool, data):
        fake = _FakeSender()
        with patch("webhooks.services.destinations.WebhookSender", return_value=fake):
            delivery = self.service.deliver(self.destination, test=test, data=data)
        return delivery, WebhookEnvelope.model_validate_json(fake.calls[0]["body"])

    def test_a_scheduled_digest_carries_its_type_and_no_test_flag(self):
        delivery, envelope = self._deliver(test=False, data=_digest())
        self.assertEqual(envelope.type, WebhookEnvelopeType.DIGEST)
        self.assertFalse(envelope.test)
        self.assertEqual(envelope.data.type, "digest")
        self.assertEqual(delivery.type, WebhookEnvelopeType.DIGEST)
        self.assertFalse(delivery.test)
        self.assertEqual(delivery.id, envelope.id)

    def test_a_test_digest_carries_the_flag_on_both_sides(self):
        delivery, envelope = self._deliver(test=True, data=_digest())
        self.assertTrue(envelope.test)
        self.assertTrue(delivery.test)
        self.assertEqual(delivery.type, WebhookEnvelopeType.DIGEST)

    def test_a_ping_is_a_ping(self):
        delivery, envelope = self._deliver(test=True, data=WebhookPingData(destination_id="d", label="CRM"))
        self.assertEqual(envelope.type, WebhookEnvelopeType.PING)
        self.assertEqual(envelope.data.label, "CRM")
        self.assertEqual(delivery.type, WebhookEnvelopeType.PING)

    def test_a_mismatched_type_and_data_cannot_be_built(self):
        with self.assertRaises(ValidationError):
            WebhookEnvelope(
                id="x",
                type="digest",
                timestamp="2026-09-16T00:00:00+00:00",
                data=WebhookPingData(destination_id="d", label="CRM"),
            )

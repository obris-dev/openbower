"""The destinations API: create with the secret shown once and stored
encrypted, the roster with its health, the refusals and their codes,
patch, delete with its children, the test delivery, and the paged log.
Session auth is real (the IdP mocked at its httpx boundary via the
shared login helper); the HTTP path is patched at the service's
sender seam.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from common.testing import TEST_IDENTITY, login_session
from lists.nodes.wait_until import WaitUntil
from lists.nodes.webhook import Webhook
from lists.services.lists import ListService
from lists.services.workflows import WorkflowService
from openbower_schema.webhooks import (
    WEBHOOK_ROTATION_GRACE_SECONDS,
    WEBHOOK_SECRET_PREFIX,
    WebhookDeliveryWire,
    WebhookDestinationCreated,
    WebhookDestinationWire,
    WebhookEnvelope,
)
from webhooks.constants import DeliveryStatus, WebhookEnvelopeType, WebhookErrorCode
from webhooks.delivery.protocol import DeliveryResult
from webhooks.models import WebhookDelivery, WebhookDestination
from webhooks.services import WebhookDestinationService
from webhooks.services.destinations import HEADERS_UNREADABLE

URL = "https://hooks.example.com/in"
HEADERS = [{"name": "Authorization", "value": "Bearer receiver-token"}]
FOREIGN_ACCOUNT = "01AC" + "Z" * 22


class _FakeSender:
    def __init__(self, result: DeliveryResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def send(self, **kwargs) -> DeliveryResult:
        self.calls.append(kwargs)
        return self.result


def _ok() -> DeliveryResult:
    return DeliveryResult(DeliveryStatus.OK, 200, "", 12, "")


class _Base(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def _create(self, **overrides) -> dict:
        body = {"label": "CRM sync", "url": URL, "headers": HEADERS, **overrides}
        resp = self.client.post(reverse("webhooks_index"), body, content_type="application/json")
        assert resp.status_code == 201, resp.content
        return resp.json()

    def _post(self, body: dict):
        return self.client.post(reverse("webhooks_index"), body, content_type="application/json")

    def _detail(self, destination_id: str) -> str:
        return reverse("webhooks_detail", kwargs={"id": destination_id})

    def _test(self, destination_id: str, result: DeliveryResult | None = None) -> tuple[dict, _FakeSender]:
        fake = _FakeSender(result or _ok())
        with patch("webhooks.services.destinations.WebhookSender", return_value=fake):
            resp = self.client.post(reverse("webhooks_test", kwargs={"id": destination_id}))
        assert resp.status_code == 200, resp.content
        return resp.json(), fake


class CreateTests(_Base):
    def test_create_returns_the_secret_once_and_stores_ciphertext(self):
        created = self._create()
        secret = created["signing_secret"]
        self.assertTrue(secret.startswith("whsec_"))
        destination = created["destination"]
        self.assertEqual(destination["header_names"], ["Authorization"])
        self.assertIsNone(destination["last_delivery"])
        self.assertNotIn("receiver-token", str(created))
        WebhookDestinationWire(**destination)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT headers, signing_secret FROM webhooks_webhookdestination WHERE id = %s", [destination["id"]]
            )
            raw_headers, raw_secret = cursor.fetchone()
        self.assertNotIn("receiver-token", raw_headers)
        self.assertNotEqual(raw_secret, secret)

        # The secret is not on any later read.
        detail = self.client.get(self._detail(destination["id"])).json()
        self.assertNotIn("signing_secret", detail)
        self.assertNotIn(secret, str(detail))
        listing = self.client.get(reverse("webhooks_index")).json()
        self.assertNotIn(secret, str(listing))

        service = WebhookDestinationService(account_id=destination_row(destination["id"]).account_id, user_id="")
        self.assertEqual(
            service.headers_of(destination_row(destination["id"])), {"Authorization": "Bearer receiver-token"}
        )

    def test_cap_refuses_with_its_code(self):
        self._create()
        with patch("webhooks.services.destinations.MAX_WEBHOOK_DESTINATIONS", 1):
            resp = self._post({"label": "Second", "url": URL})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], WebhookErrorCode.DESTINATIONS_FULL)
        self.assertEqual(WebhookDestination.objects.count(), 1)

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True, WEBHOOK_REQUIRE_HTTPS=True)
    def test_blocked_urls_refuse_with_their_code(self):
        for url in ("http://hooks.example.com/in", "https://10.0.0.1/in", "https://[::1]/in"):
            with self.subTest(url=url):
                resp = self._post({"label": "Blocked", "url": url})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"], WebhookErrorCode.URL_BLOCKED)
        self.assertEqual(WebhookDestination.objects.count(), 0)

    def test_reserved_header_refuses_with_its_code(self):
        resp = self._post({"label": "Forged", "url": URL, "headers": [{"name": "Webhook-Signature", "value": "v1,x"}]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], WebhookErrorCode.HEADER_RESERVED)
        self.assertIn("Webhook-Signature", resp.json()["detail"])

    def test_shape_refusals(self):
        # Shape rules are the serializer's: a plain 400, mirrored client-side.
        bad_bodies = [
            {"label": "x", "url": "ftp://hooks.example.com/in"},
            {"label": "x", "url": URL, "headers": [{"name": "bad name", "value": "v"}]},
            {"label": "x", "url": URL, "headers": [{"name": "X-A", "value": "a\nb"}]},
            # A TRAILING newline in a value (kept verbatim, never
            # trimmed): Python's `$` matches before it, so the grammar
            # must be applied as a full match or this stores and then
            # fails on every send. Names are trimmed like every label,
            # so a trailing newline there is simply dropped.
            {"label": "x", "url": URL, "headers": [{"name": "X-A", "value": "a\n"}]},
            {"label": "x", "url": URL, "headers": [{"name": "X-A", "value": "a"}, {"name": "x-a", "value": "b"}]},
            {"label": "x", "url": URL, "headers": [{"name": f"X-{i}", "value": "v"} for i in range(9)]},
            {"label": "", "url": URL},
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.assertEqual(self._post(body).status_code, 400)
        self.assertEqual(WebhookDestination.objects.count(), 0)


class ReadTests(_Base):
    def test_roster_and_detail_carry_the_newest_delivery(self):
        created = self._create()["destination"]
        other = self._create(label="Other")["destination"]
        self._test(created["id"], DeliveryResult(DeliveryStatus.TRANSIENT, 503, "down", 5, "boom"))
        delivery, _ = self._test(created["id"])

        listing = self.client.get(reverse("webhooks_index")).json()
        by_id = {item["id"]: item for item in listing["items"]}
        self.assertEqual(by_id[created["id"]]["last_delivery"]["id"], delivery["id"])
        self.assertEqual(by_id[created["id"]]["last_delivery"]["status"], "ok")
        self.assertIsNone(by_id[other["id"]]["last_delivery"])
        self.assertEqual(self.client.get(self._detail(created["id"])).json()["last_delivery"]["id"], delivery["id"])

    def test_foreign_reads_as_missing(self):
        created = self._create()["destination"]
        WebhookDestination.objects.filter(id=created["id"]).update(account_id=FOREIGN_ACCOUNT)
        self.assertEqual(self.client.get(self._detail(created["id"])).status_code, 404)
        self.assertEqual(self.client.post(reverse("webhooks_test", kwargs={"id": created["id"]})).status_code, 404)
        self.assertEqual(self.client.get(reverse("webhooks_deliveries", kwargs={"id": created["id"]})).status_code, 404)
        self.assertEqual(self.client.get(reverse("webhooks_index")).json()["items"], [])

    def test_unauthenticated_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get(reverse("webhooks_index")).status_code, 401)


class PatchTests(_Base):
    def _patch(self, destination_id: str, body: dict):
        return self.client.patch(self._detail(destination_id), body, content_type="application/json")

    def test_patch_each_field(self):
        created = self._create()["destination"]
        renamed = self._patch(created["id"], {"label": "Renamed", "enabled": False}).json()
        self.assertEqual(renamed["label"], "Renamed")
        self.assertFalse(renamed["enabled"])
        self.assertEqual(renamed["url"], URL)

        replaced = self._patch(created["id"], {"headers": [{"name": "X-Key", "value": "k"}]}).json()
        self.assertEqual(replaced["header_names"], ["X-Key"])
        service = WebhookDestinationService(account_id=destination_row(created["id"]).account_id, user_id="")
        self.assertEqual(service.headers_of(destination_row(created["id"])), {"X-Key": "k"})

        cleared = self._patch(created["id"], {"headers": []}).json()
        self.assertEqual(cleared["header_names"], [])

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True)
    def test_patch_re_guards_the_url(self):
        created = self._create()["destination"]
        resp = self._patch(created["id"], {"url": "https://127.0.0.1/in"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], WebhookErrorCode.URL_BLOCKED)
        self.assertEqual(destination_row(created["id"]).url, URL)

    def test_patch_refuses_a_reserved_header_and_an_empty_change(self):
        created = self._create()["destination"]
        resp = self._patch(created["id"], {"headers": [{"name": "content-type", "value": "text/plain"}]})
        self.assertEqual(resp.json()["error"], WebhookErrorCode.HEADER_RESERVED)
        self.assertEqual(self._patch(created["id"], {}).status_code, 400)


class DeleteTests(_Base):
    def test_delete_takes_the_deliveries_with_it(self):
        created = self._create()["destination"]
        self._test(created["id"])
        self.assertEqual(WebhookDelivery.objects.count(), 1)
        self.assertEqual(self.client.delete(self._detail(created["id"])).status_code, 204)
        self.assertEqual(self.client.delete(self._detail(created["id"])).status_code, 404)
        self.assertEqual(WebhookDelivery.objects.count(), 0)
        self.assertEqual(WebhookDestination.objects.count(), 0)


class RotateTests(_Base):
    def test_rotate_and_delete_take_the_destination_row_lock(self):
        """FAILS if either write stops locking the row: two rotates
        could both mint against one secret, and a delete could land
        under a column binding to it."""
        created = self._create()["destination"]
        with CaptureQueriesContext(connection) as rotate_queries:
            self.client.post(reverse("webhooks_rotate", kwargs={"id": created["id"]}))
        self.assertTrue(any("FOR UPDATE" in q["sql"] for q in rotate_queries.captured_queries))
        with CaptureQueriesContext(connection) as delete_queries:
            self.client.delete(self._detail(created["id"]))
        self.assertTrue(any("FOR UPDATE" in q["sql"] for q in delete_queries.captured_queries))

    def test_rotate_mints_a_new_secret_shown_once_and_keeps_the_old_one_signing(self):
        created = self._create()
        old_secret = created["signing_secret"]
        resp = self.client.post(reverse("webhooks_rotate", kwargs={"id": created["destination"]["id"]}))
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        WebhookDestinationCreated(**body)
        self.assertTrue(body["signing_secret"].startswith(WEBHOOK_SECRET_PREFIX))
        self.assertNotEqual(body["signing_secret"], old_secret)
        self.assertIsNotNone(body["destination"]["rotated_at"])
        stored = WebhookDestination.objects.get(id=created["destination"]["id"])
        self.assertEqual((stored.signing_secret, stored.previous_signing_secret), (body["signing_secret"], old_secret))
        # The next delivery signs with both, current first.
        _, fake = self._test(created["destination"]["id"])
        self.assertEqual(fake.calls[0]["secrets"], [body["signing_secret"], old_secret])

    def test_a_second_rotation_inside_the_grace_window_is_refused_until_it_closes(self):
        created = self._create()
        rotate_url = reverse("webhooks_rotate", kwargs={"id": created["destination"]["id"]})
        first = self.client.post(rotate_url)
        self.assertEqual(first.status_code, 200, first.content)
        second = self.client.post(rotate_url)
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(second.json()["error"], WebhookErrorCode.ROTATION_IN_PROGRESS)
        # The window closes: the next rotation lands and retires the first's secret.
        WebhookDestination.objects.filter(id=created["destination"]["id"]).update(
            rotated_at=timezone.now() - timedelta(seconds=WEBHOOK_ROTATION_GRACE_SECONDS + 1)
        )
        third = self.client.post(rotate_url)
        self.assertEqual(third.status_code, 200, third.content)
        stored = WebhookDestination.objects.get(id=created["destination"]["id"])
        self.assertEqual(stored.previous_signing_secret, first.json()["signing_secret"])

    def test_the_retired_secret_stops_signing_after_the_grace_window(self):
        created = self._create()["destination"]
        self.client.post(reverse("webhooks_rotate", kwargs={"id": created["id"]}))
        stored = WebhookDestination.objects.get(id=created["id"])
        # Just inside the window: both secrets still sign.
        stored.rotated_at = timezone.now() - timedelta(seconds=WEBHOOK_ROTATION_GRACE_SECONDS - 1)
        stored.save(update_fields=["rotated_at"])
        _, fake = self._test(created["id"])
        self.assertEqual(len(fake.calls[0]["secrets"]), 2)
        stored.rotated_at = timezone.now() - timedelta(seconds=WEBHOOK_ROTATION_GRACE_SECONDS + 1)
        stored.save(update_fields=["rotated_at"])
        _, fake = self._test(created["id"])
        self.assertEqual(len(fake.calls[0]["secrets"]), 1)


class InUseDeleteTests(_Base):
    def _column_using(self, destination_id: str) -> str:
        lists = ListService(account_id=TEST_IDENTITY["account_id"])
        sheet = lists.create(owner_id=TEST_IDENTITY["id"], label="Prospects", columns=[], origin="manual")
        workflows = WorkflowService(account_id=TEST_IDENTITY["account_id"])
        path, _ = workflows.create_path(
            sheet, [WaitUntil(inbound_path_ids=[]), Webhook(destination_id=destination_id, payload_keys=[])]
        )
        return str(path.id)

    def test_delete_is_refused_while_a_webhook_column_sends_here_and_the_wire_counts_it(self):
        created = self._create()["destination"]
        path_id = self._column_using(created["id"])
        # A second column on a second sheet: the count is over the
        # destination's nodes, never one row's.
        second_path_id = self._column_using(created["id"])
        detail = self.client.get(self._detail(created["id"])).json()
        self.assertEqual(detail["column_count"], 2)
        # The roster does not count: null, never a zero that reads as unused.
        [listed] = self.client.get(reverse("webhooks_index")).json()["items"]
        self.assertIsNone(listed["column_count"])
        resp = self.client.delete(self._detail(created["id"]))
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.json()["error"], WebhookErrorCode.DESTINATION_IN_USE)
        self.assertIn("2 webhook columns on 2 sheets", resp.json()["detail"])
        self.assertEqual(WebhookDestination.objects.count(), 1)
        workflows = WorkflowService(account_id=TEST_IDENTITY["account_id"])
        workflows.delete_path(path_id)
        workflows.delete_path(second_path_id)
        self.assertEqual(self.client.delete(self._detail(created["id"])).status_code, 204)


class TestDeliveryTests(_Base):
    def test_sends_a_signed_envelope_and_records_it(self):
        created = self._create()["destination"]
        delivery, fake = self._test(created["id"])
        WebhookDeliveryWire(**delivery)
        self.assertEqual(delivery["status"], "ok")
        self.assertEqual(delivery["type"], "ping")
        self.assertTrue(delivery["test"])
        self.assertEqual(delivery["http_status"], 200)
        self.assertEqual(delivery["destination_id"], created["id"])

        call = fake.calls[0]
        self.assertEqual(call["url"], URL)
        self.assertEqual(call["headers"], {"Authorization": "Bearer receiver-token"})
        self.assertEqual(len(call["secrets"]), 1)
        self.assertTrue(call["secrets"][0].startswith("whsec_"))
        self.assertEqual(call["delivery_id"], delivery["id"])
        envelope = WebhookEnvelope.model_validate_json(call["body"])
        self.assertEqual(envelope.id, delivery["id"])
        self.assertEqual(envelope.type, WebhookEnvelopeType.PING)
        self.assertTrue(envelope.test)
        self.assertEqual(
            envelope.data.model_dump(), {"type": "ping", "destination_id": created["id"], "label": "CRM sync"}
        )

        row = WebhookDelivery.objects.get(id=delivery["id"])
        self.assertEqual(row.destination_id, created["id"])

    def test_a_failed_delivery_is_still_a_200_with_its_facts(self):
        created = self._create()["destination"]
        delivery, _ = self._test(created["id"], DeliveryResult(DeliveryStatus.REJECTED, 404, "Refused.", 8, "nope"))
        self.assertEqual(delivery["status"], "rejected")
        self.assertEqual(delivery["http_status"], 404)
        self.assertEqual(delivery["error"], "Refused.")
        self.assertEqual(delivery["response_excerpt"], "nope")

    def test_a_disabled_destination_still_tests(self):
        created = self._create()["destination"]
        self.client.patch(self._detail(created["id"]), {"enabled": False}, content_type="application/json")
        delivery, fake = self._test(created["id"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(delivery["status"], "ok")

    def test_unreadable_headers_block_without_a_request(self):
        created = self._create()["destination"]
        # What a rotated encryption key leaves behind: a column that no
        # longer decodes to the JSON the service wrote.
        WebhookDestination.objects.filter(id=created["id"]).update(headers="not json")
        delivery, fake = self._test(created["id"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(delivery["status"], "blocked")
        self.assertEqual(delivery["error"], HEADERS_UNREADABLE)
        self.assertEqual(self.client.get(self._detail(created["id"])).json()["header_names"], [])


class DeliveriesPageTests(_Base):
    def test_pages_newest_first_by_keyset(self):
        created = self._create()["destination"]
        ids = [self._test(created["id"])[0]["id"] for _ in range(3)]
        route = reverse("webhooks_deliveries", kwargs={"id": created["id"]})
        first = self.client.get(route, {"limit": 2}).json()
        self.assertEqual([item["id"] for item in first["items"]], [ids[2], ids[1]])
        self.assertEqual(first["next_cursor"], ids[1])
        second = self.client.get(route, {"limit": 2, "after": first["next_cursor"]}).json()
        self.assertEqual([item["id"] for item in second["items"]], [ids[0]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.client.get(route, {"limit": "lots"}).status_code, 400)


def destination_row(destination_id: str) -> WebhookDestination:
    return WebhookDestination.objects.get(id=destination_id)

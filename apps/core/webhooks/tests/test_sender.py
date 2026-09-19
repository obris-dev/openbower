"""The sender: the status table, the transport failures, what it puts on
the wire (signature, the user's headers, the ones it owns), the bounded
and masked excerpt, the guards that stop a request before it leaves,
and the URL, headers, and body kept out of logs. httpx runs for real
over a mock transport.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import socket
from collections.abc import Callable, Iterable
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings

from webhooks.constants import WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH, DeliveryStatus
from webhooks.delivery import client as client_module
from webhooks.delivery import sender as sender_module
from webhooks.delivery.client import reset_shared_client, shared_client
from webhooks.delivery.sender import (
    BLOCKED,
    REDACTED,
    REDIRECT,
    REJECTED,
    SECRET_UNREADABLE,
    TIMED_OUT,
    TRANSIENT,
    UNREACHABLE,
    UNSENDABLE,
    USER_AGENT,
    WebhookSender,
)

URL = "https://hooks.example.com/in?token=s3cret-in-url"
SECRET = "whsec_" + base64.b64encode(bytes(24)).decode("ascii")
BODY = b'{"id":"01J","type":"test"}'
HEADERS = {"Authorization": "Bearer receiver-token", "X-Trace": "abc"}
DELIVERY_ID = "01DLV" + "A" * 21


def _addrinfo(address: str) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]


class _Transport:
    """A mock transport that records the request and answers as told:
    a status with a body, or an exception."""

    def __init__(
        self,
        status: int = 200,
        body: bytes | Iterable[bytes] | Callable[[httpx.Request], bytes] = b"",
        raise_: Exception | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.raise_ = raise_
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_ is not None:
            raise self.raise_
        # A callable body answers from the request: a receiver that
        # echoes what it was sent.
        body = self.body(request) if callable(self.body) else self.body
        return httpx.Response(self.status, content=body)

    def __enter__(self):
        # The shared client is built once per process: reset around the
        # patch so this transport is what the next build picks up, and
        # nothing built here outlives the test.
        reset_shared_client()
        self._patch = patch.object(client_module, "_transport", return_value=httpx.MockTransport(self.handler))
        self._patch.start()
        return self

    def __exit__(self, *exc) -> None:
        self._patch.stop()
        reset_shared_client()


def _send(**overrides):
    fields = {"url": URL, "headers": HEADERS, "secrets": [SECRET], "delivery_id": DELIVERY_ID, "body": BODY}
    fields.update(overrides)
    return WebhookSender().send(**fields)


class StatusTableTests(SimpleTestCase):
    def _run(self, status: int, body: bytes = b""):
        with _Transport(status, body):
            return _send()

    def test_2xx_is_ok_and_keeps_no_excerpt(self):
        result = self._run(201, b"received")
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(result.http_status, 201)
        self.assertEqual(result.error, "")
        self.assertEqual(result.response_excerpt, "")

    def test_redirect_is_rejected(self):
        result = self._run(302)
        self.assertEqual(result.status, DeliveryStatus.REJECTED)
        self.assertEqual(result.error, REDIRECT)

    def test_client_error_is_rejected(self):
        result = self._run(404, b"no such hook")
        self.assertEqual(result.status, DeliveryStatus.REJECTED)
        self.assertEqual(result.error, REJECTED)
        self.assertEqual(result.response_excerpt, "no such hook")

    def test_retryable_4xx_and_5xx_are_transient(self):
        for status in (408, 429, 500, 503):
            with self.subTest(status=status):
                result = self._run(status)
                self.assertEqual(result.status, DeliveryStatus.TRANSIENT)
                self.assertEqual(result.error, TRANSIENT)
                self.assertEqual(result.http_status, status)


class TransportFailureTests(SimpleTestCase):
    def _run(self, exc: Exception):
        with _Transport(raise_=exc):
            return _send()

    def test_timeout_is_transient_with_no_status(self):
        result = self._run(httpx.ConnectTimeout("slow"))
        self.assertEqual(result.status, DeliveryStatus.TRANSIENT)
        self.assertIsNone(result.http_status)
        self.assertEqual(result.error, TIMED_OUT)

    def test_connection_failure_is_transient(self):
        result = self._run(httpx.ConnectError("refused"))
        self.assertEqual(result.status, DeliveryStatus.TRANSIENT)
        self.assertEqual(result.error, UNREACHABLE)

    def test_protocol_failure_is_rejected(self):
        # A request the HTTP layer refuses to speak fails the same way
        # on every retry: a rejection, not a transient.
        result = self._run(httpx.LocalProtocolError("bad header"))
        self.assertEqual(result.status, DeliveryStatus.REJECTED)
        self.assertEqual(result.error, UNSENDABLE)


class RequestShapeTests(SimpleTestCase):
    def test_body_headers_and_signature(self):
        with _Transport(200) as transport:
            _send()
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.content, BODY)
        self.assertEqual(request.headers["user-agent"], USER_AGENT)
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["authorization"], "Bearer receiver-token")
        self.assertEqual(request.headers["x-trace"], "abc")
        self.assertEqual(request.headers["webhook-id"], DELIVERY_ID)
        timestamp = request.headers["webhook-timestamp"]
        expected = hmac.new(bytes(24), f"{DELIVERY_ID}.{timestamp}.".encode() + BODY, hashlib.sha256).digest()
        self.assertEqual(request.headers["webhook-signature"], "v1," + base64.b64encode(expected).decode("ascii"))

    def test_a_stored_reserved_header_never_wins(self):
        # Refused at write; guarded again here so a stored row (a
        # past grammar, a raw insert) cannot forge the signature or
        # the framing.
        headers = {**HEADERS, "Webhook-Signature": "v1,forged", "Content-Type": "text/plain", "user-agent": "x"}
        with _Transport(200) as transport:
            _send(headers=headers)
        request = transport.requests[0]
        self.assertNotEqual(request.headers["webhook-signature"], "v1,forged")
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["user-agent"], USER_AGENT)

    @override_settings(WEBHOOK_TIMEOUT_SECONDS=7)
    def test_timeout_phases_are_pinned(self):
        with _Transport(200) as transport:
            _send()
        self.assertEqual(transport.requests[0].extensions["timeout"], {"connect": 4, "read": 7, "write": 4, "pool": 4})

    def test_excerpt_is_bounded_and_masked(self):
        # An echo receiver returns the request: the configured header
        # values and the signature must not land in the log.
        echo = b"Authorization: Bearer receiver-token; X-Trace: abc; " + b"x" * (
            WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH * 4
        )
        with _Transport(500, echo) as transport:
            result = _send()
        signature = transport.requests[0].headers["webhook-signature"]
        self.assertLessEqual(len(result.response_excerpt), WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH)
        self.assertNotIn("receiver-token", result.response_excerpt)
        self.assertNotIn(signature, result.response_excerpt)
        self.assertIn(REDACTED, result.response_excerpt)

    def test_a_masked_value_straddling_the_cut_leaves_no_prefix(self):
        # The cut used to land before masking, so a token spanning it
        # kept its head. The value starts 10 bytes before the cap.
        body = b"x" * (WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH - 10) + b"Bearer receiver-token" + b"y" * 10
        with _Transport(500, body):
            result = _send()
        self.assertNotIn("Bearer r", result.response_excerpt)
        self.assertIn(REDACTED, result.response_excerpt)
        self.assertLessEqual(len(result.response_excerpt), WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH)

    def test_a_dripping_receiver_is_cut_off_by_the_read_budget(self):
        # The read timeout bounds one socket read, never the whole body:
        # a receiver dripping bytes just inside it would hold the worker
        # for the whole cap. The sender's own clock ends it.
        # started, after chunk 1 (inside the budget), after chunk 2
        # (past it), then the duration read.
        clock = iter([0.0, 0.0, 100.0, 101.0, 102.0])
        with (
            _Transport(500, iter([b"1", b"2", b"3"])),
            patch.object(sender_module.time, "monotonic", side_effect=lambda: next(clock)),
        ):
            result = _send()
        self.assertEqual(result.response_excerpt, "12")

    def test_a_small_body_is_read_to_the_end_on_success(self):
        # A body left unread closes the connection instead of pooling
        # it; a 2xx answer within the cap is drained (and discarded).
        exhausted: list[bool] = []

        def body():
            yield b"received"
            exhausted.append(True)

        with _Transport(200, body()):
            result = _send()
        self.assertEqual(result.response_excerpt, "")
        self.assertEqual(exhausted, [True])

    def test_logs_carry_neither_url_nor_credentials_nor_body(self):
        with _Transport(503, b"down"), self.assertLogs("webhooks.delivery.sender", level="WARNING") as logs:
            _send()
        with (
            _Transport(raise_=httpx.ConnectError("refused")),
            self.assertLogs("webhooks.delivery.sender", level="WARNING") as failure_logs,
        ):
            _send()
        joined = "\n".join(logs.output + failure_logs.output)
        for forbidden in ("hooks.example.com", "s3cret-in-url", "receiver-token", BODY.decode(), "down"):
            self.assertNotIn(forbidden, joined)
        self.assertIn(DELIVERY_ID, joined)


class SharedClientTests(SimpleTestCase):
    def test_sends_share_one_client_until_reset(self):
        # The pool and the SSL context are built once per process: two
        # sends construct one client, and only a reset builds another.
        with _Transport(200) as transport, patch.object(client_module.httpx, "Client", wraps=httpx.Client) as built:
            _send()
            _send()
            self.assertEqual(built.call_count, 1)
            self.assertIs(shared_client(), shared_client())
            reset_shared_client()
            _send()
            self.assertEqual(built.call_count, 2)
        self.assertEqual(len(transport.requests), 3)


class GuardTests(SimpleTestCase):
    def test_unreadable_secret_blocks_before_any_request(self):
        with _Transport(200) as transport:
            result = _send(secrets=["gAAAAABciphertext"])
        self.assertEqual(result.status, DeliveryStatus.BLOCKED)
        self.assertEqual(result.error, SECRET_UNREADABLE)
        self.assertEqual(transport.requests, [])

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True)
    def test_send_time_guard_resolves_and_blocks(self):
        with (
            _Transport(200) as transport,
            patch("common.ssrf.socket.getaddrinfo", return_value=_addrinfo("10.0.0.7")) as resolve,
            self.assertLogs("webhooks.delivery.sender", level="WARNING") as logs,
        ):
            result = _send()
        resolve.assert_called_once()
        self.assertEqual(result.status, DeliveryStatus.BLOCKED)
        # The user reads the reason (their own URL's host); the log
        # does not.
        self.assertTrue(result.error.startswith(BLOCKED))
        self.assertIn("10.0.0.7", result.error)
        self.assertNotIn("hooks.example.com", "\n".join(logs.output))
        self.assertEqual(transport.requests, [])

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True)
    def test_send_time_guard_lets_a_public_host_through(self):
        with (
            _Transport(200) as transport,
            patch("common.ssrf.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")),
        ):
            result = _send()
        self.assertEqual(result.status, DeliveryStatus.OK)
        self.assertEqual(len(transport.requests), 1)


class RotationSecretsTests(SimpleTestCase):
    """After a rotation two secrets sign; a retired one that is
    unreadable is dropped, never blocks."""

    def test_two_readable_secrets_put_two_values_in_the_header_both_masked(self):
        other = "whsec_" + base64.b64encode(bytes(range(24))).decode("ascii")
        # The receiver echoes the signature header back, so the excerpt
        # would carry both values were they not masked.
        echo = lambda request: b"echo " + request.headers["webhook-signature"].encode()  # noqa: E731
        with _Transport(500, echo) as transport:
            result = _send(secrets=[SECRET, other])
        self.assertEqual(result.status, DeliveryStatus.TRANSIENT)
        header = transport.requests[0].headers["webhook-signature"]
        values = header.split(" ")
        self.assertEqual(len(values), 2)
        self.assertTrue(all(value.startswith("v1,") for value in values))
        for value in values:
            self.assertNotIn(value, result.response_excerpt)
        self.assertEqual(result.response_excerpt.count(REDACTED), 2)

    def test_an_unreadable_retired_secret_is_dropped_and_the_current_one_signs(self):
        with _Transport(200, b"") as transport:
            result = _send(secrets=[SECRET, "gAAAAABciphertext"])
        self.assertEqual(result.status, DeliveryStatus.OK)
        header = transport.requests[0].headers["webhook-signature"]
        self.assertEqual(len(header.split(" ")), 1)

    def test_an_unreadable_current_secret_blocks_whatever_follows(self):
        with _Transport(200, b""):
            result = _send(secrets=["gAAAAABciphertext", SECRET])
        self.assertEqual(result.status, DeliveryStatus.BLOCKED)

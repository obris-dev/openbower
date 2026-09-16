"""The one HTTP path a delivery takes. Guards, signs, POSTs once, and
classifies the answer; it never retries (the caller's schedule owns
that) and never logs the URL, a header, the secret, or the body: a
user's endpoint and credentials are theirs."""

from __future__ import annotations

import logging
import time

import httpx
from django.conf import settings

from common.ssrf import destination_block_reason

from ..constants import (
    RESERVED_WEBHOOK_HEADER_NAMES,
    WEBHOOK_CONNECT_TIMEOUT_SECONDS,
    WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH,
    DeliveryStatus,
)
from .client import shared_client
from .protocol import DeliveryResult
from .sign import secret_key, sign

logger = logging.getLogger(__name__)

USER_AGENT = "OpenBower-webhooks/1"
REDACTED = "[redacted]"

# The sentences a delivery's `error` carries, one per way it can end.
# They state what happened, never what happens next: whether an
# attempt is retried is the caller's lane (a test is one attempt; the
# flush retries a transient outcome on its own schedule).
TRANSIENT = "The receiver answered with an error."
TIMED_OUT = "The receiver did not answer in time."
UNREACHABLE = "The receiver could not be reached."
REJECTED = "The receiver refused the delivery."
REDIRECT = "The receiver answered with a redirect, which a delivery never follows."
UNSENDABLE = "The request could not be sent to this destination."
BLOCKED = "The receiver's address is not reachable from this deployment"
SECRET_UNREADABLE = "This destination's signing secret is unreadable; delete it and add it again."

_RETRYABLE_4XX = frozenset({408, 429})


def classify(status: int) -> tuple[DeliveryStatus, str]:
    """The status table: what an HTTP answer means for a retry."""
    if 200 <= status < 300:
        return DeliveryStatus.OK, ""
    if 300 <= status < 400:
        return DeliveryStatus.REJECTED, REDIRECT
    if status >= 500 or status in _RETRYABLE_4XX:
        return DeliveryStatus.TRANSIENT, TRANSIENT
    return DeliveryStatus.REJECTED, REJECTED


def redact(text: str, secrets: list[str]) -> str:
    """Mask every configured value out of a receiver's echo. Short values
    are masked too: a masked "x" costs nothing, an unmasked token does."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


class WebhookSender:
    def send(self, *, url: str, headers: dict[str, str], secret: str, delivery_id: str, body: bytes) -> DeliveryResult:
        key = secret_key(secret)
        if key is None:
            logger.warning("delivery %s: blocked, signing secret unreadable", delivery_id)
            return DeliveryResult(DeliveryStatus.BLOCKED, None, SECRET_UNREADABLE)
        reason = destination_block_reason(
            url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            resolve_dns=True,
        )
        if reason is not None:
            # The DNS answer checked here can differ from the one the
            # connect below gets (a rebinding host); the residual is
            # accepted rather than pinning the connect to an address.
            # The reason names the host, so it goes to the user (it is
            # their URL) and not to the log.
            logger.warning("delivery %s: blocked at send", delivery_id)
            return DeliveryResult(DeliveryStatus.BLOCKED, None, f"{BLOCKED} ({reason}).")

        # The user's headers first, then the ones this sender owns, so a
        # stored reserved name (refused at write, guarded again here)
        # can never win.
        user_headers = {
            name: value for name, value in headers.items() if name.lower() not in RESERVED_WEBHOOK_HEADER_NAMES
        }
        signature = sign(key=key, delivery_id=delivery_id, timestamp=int(time.time()), body=body)
        request_headers = {
            **user_headers,
            "user-agent": USER_AGENT,
            "content-type": "application/json",
            **signature.headers(),
        }
        masked = [*user_headers.values(), signature.value]
        timeout = httpx.Timeout(
            connect=WEBHOOK_CONNECT_TIMEOUT_SECONDS,
            read=settings.WEBHOOK_TIMEOUT_SECONDS,
            write=WEBHOOK_CONNECT_TIMEOUT_SECONDS,
            pool=WEBHOOK_CONNECT_TIMEOUT_SECONDS,
        )
        started = time.monotonic()
        try:
            with shared_client().stream(
                "POST", url, content=body, headers=request_headers, timeout=timeout
            ) as response:
                status, error = classify(response.status_code)
                # Read on every status, so a body within the cap is
                # drained and its connection returns to the pool; only a
                # failure's answer is kept.
                head = _head(response, masked, started=started, budget_seconds=settings.WEBHOOK_TIMEOUT_SECONDS)
                excerpt = "" if status is DeliveryStatus.OK else head
        except httpx.TimeoutException as e:
            return self._failed(delivery_id, started, TIMED_OUT, e)
        except (httpx.LocalProtocolError, httpx.InvalidURL) as e:
            # The request itself could not be spoken (a header the HTTP
            # layer refuses, a URL it cannot parse): a retry cannot fix
            # it, so it is a rejection, never a transient.
            return self._failed(delivery_id, started, UNSENDABLE, e, status=DeliveryStatus.REJECTED)
        except httpx.TransportError as e:
            return self._failed(delivery_id, started, UNREACHABLE, e)
        except httpx.HTTPError as e:
            return self._failed(delivery_id, started, UNSENDABLE, e, status=DeliveryStatus.REJECTED)
        duration_ms = _elapsed_ms(started)
        if status is not DeliveryStatus.OK:
            logger.warning("delivery %s: %s (http %d)", delivery_id, status, response.status_code)
        return DeliveryResult(status, response.status_code, error, duration_ms, excerpt)

    def _failed(
        self,
        delivery_id: str,
        started: float,
        error: str,
        exc: Exception,
        *,
        status: DeliveryStatus = DeliveryStatus.TRANSIENT,
    ) -> DeliveryResult:
        logger.warning("delivery %s: %s (%s)", delivery_id, status, type(exc).__name__)
        return DeliveryResult(status, None, error, _elapsed_ms(started))


def _head(response: httpx.Response, masked: list[str], *, started: float, budget_seconds: float) -> str:
    """The first WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH characters of the
    answer, masked. Read in chunks and stopped at a byte budget or when
    the delivery's read budget (counted from `started`) is spent: the
    transport's read timeout bounds one socket read, never the body, so
    a receiver dripping bytes would otherwise hold the worker for the
    whole cap. The byte budget is the cap plus the longest masked value,
    so a value straddling the cap is masked whole rather than leaving
    its head; iter_bytes yields decoded bytes, so a compressed body
    cannot expand past it. Decoded with replacement (a binary answer
    must not raise here), masked, then cut to the cap."""
    reserve = max((len(value.encode("utf-8")) for value in masked), default=0)
    collected = bytearray()
    for chunk in response.iter_bytes():
        collected.extend(chunk)
        if len(collected) >= WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH + reserve:
            break
        if time.monotonic() - started > budget_seconds:
            break
    text = collected.decode("utf-8", errors="replace").strip()
    return redact(text, masked)[:WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH]


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)

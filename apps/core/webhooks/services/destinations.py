"""Account-scoped custody of webhook destinations: the one writer of
the table, the one place its encrypted columns are decoded, and the
test delivery (which records through the deliveries service)."""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

import ulid
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.ssrf import destination_block_reason
from openbower_schema.webhooks import WebhookEnvelope, WebhookEnvelopeData, WebhookPingData

from ..constants import (
    MAX_WEBHOOK_DESTINATIONS,
    RESERVED_WEBHOOK_HEADER_NAMES,
    WEBHOOK_ROTATION_GRACE_SECONDS,
    DeliveryStatus,
    WebhookEnvelopeType,
    WebhookErrorCode,
)
from ..delivery.protocol import DeliveryResult
from ..delivery.sender import WebhookSender
from ..delivery.sign import encode_body, mint_secret
from ..models import WebhookDelivery, WebhookDestination
from .deliveries import WebhookDeliveryService

logger = logging.getLogger(__name__)


def envelope_of(*, test: bool, data: WebhookEnvelopeData) -> WebhookEnvelope:
    """One envelope, minted now: the id (which is the delivery's id when
    it is sent) and the clock are written HERE and nowhere else, so a
    preview and a send of the same data differ only in being sent."""
    return WebhookEnvelope(
        id=str(ulid.ulid()),
        type=data.type,
        test=test,
        timestamp=timezone.now().isoformat(),
        data=data,
    )


class Sent(NamedTuple):
    """One delivery as recorded, with the envelope it carried."""

    delivery: WebhookDelivery
    envelope: WebhookEnvelope


HEADERS_UNREADABLE = "This destination's headers are unreadable; delete it and add it again."


class WebhookRefused(Exception):
    """Base for refusals: `code` is the machine leg the view maps to a
    status, str(self) is server-authored copy the client renders
    verbatim."""

    code = WebhookErrorCode.URL_BLOCKED


class DestinationsFull(WebhookRefused):
    code = WebhookErrorCode.DESTINATIONS_FULL

    def __init__(self) -> None:
        super().__init__(f"This account has {MAX_WEBHOOK_DESTINATIONS} destinations; delete one to add another.")


class UrlBlocked(WebhookRefused):
    code = WebhookErrorCode.URL_BLOCKED

    def __init__(self, reason: str) -> None:
        super().__init__(f"That URL cannot be used: {reason}.")


class HeaderReserved(WebhookRefused):
    code = WebhookErrorCode.HEADER_RESERVED

    def __init__(self, name: str) -> None:
        super().__init__(f"The {name} header is set by every delivery and cannot be configured.")


class DestinationInUse(WebhookRefused):
    """A Send webhook column sends here: deleting would strand it, so
    those columns go first (a 409: the fix is elsewhere)."""

    code = WebhookErrorCode.DESTINATION_IN_USE

    def __init__(self, *, columns: int, sheets: int) -> None:
        which = "1 webhook column" if columns == 1 else f"{columns} webhook columns"
        where = "1 sheet" if sheets == 1 else f"{sheets} sheets"
        super().__init__(f"{which} on {where} send to this destination; edit or delete those columns first.")


class DestinationNotFound(Exception):
    """Missing OR foreign destination (a cross-tenant read is not-found)."""


class WebhookDestinationService:
    def __init__(self, *, account_id: str, user_id: str) -> None:
        self.account_id = account_id
        self.user_id = user_id
        self.deliveries = WebhookDeliveryService(account_id=account_id)

    def list_for(self) -> list[WebhookDestination]:
        return list(WebhookDestination.objects.filter(account_id=self.account_id).order_by("-id"))

    def get(self, destination_id: str) -> WebhookDestination:
        try:
            return WebhookDestination.objects.get(id=destination_id, account_id=self.account_id)
        except WebhookDestination.DoesNotExist as e:
            raise DestinationNotFound(destination_id) from e

    def create(self, *, label: str, url: str, headers: dict[str, str]) -> tuple[WebhookDestination, str]:
        """Returns `(destination, raw_secret)`: the secret exists in
        plaintext only in this return value and the create response."""
        self._guard_url(url)
        self._guard_headers(headers)
        secret = mint_secret()
        # An unlocked count, the agents roster's cap: the bound keeps the
        # unpaged roster small, and a concurrent burst overshooting it by
        # a few rows harms nothing worth a lock across the account.
        if WebhookDestination.objects.filter(account_id=self.account_id).count() >= MAX_WEBHOOK_DESTINATIONS:
            raise DestinationsFull()
        destination = WebhookDestination.objects.create(
            account_id=self.account_id,
            user_id=self.user_id,
            label=label,
            url=url,
            headers=json.dumps(headers),
            signing_secret=secret,
        )
        return destination, secret

    def patch(
        self,
        destination: WebhookDestination,
        *,
        label: str | None = None,
        url: str | None = None,
        enabled: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> WebhookDestination:
        """Any subset; `headers` present replaces the whole set (values
        are never readable, so there is no per-header edit)."""
        updates: list[str] = []
        if label is not None:
            destination.label = label
            updates.append("label")
        if url is not None:
            self._guard_url(url)
            destination.url = url
            updates.append("url")
        if enabled is not None:
            destination.enabled = enabled
            updates.append("enabled")
        if headers is not None:
            self._guard_headers(headers)
            destination.headers = json.dumps(headers)
            updates.append("headers")
        if updates:
            destination.save(update_fields=[*updates, "updated_at"])
        return destination

    def delete(self, destination: WebhookDestination) -> None:
        """No cascades: the owner removes its own children, in one
        transaction. Refused while any webhook column sends here (the
        column's config names this id; the sheet's service owns that
        link, read here through the substrate's one config reader). A
        zero count means another delete won. A delivery recorded in the
        same instant can outlive this; the prune removes it."""
        users = self._webhook_nodes_naming(destination)
        if users:
            sheets = len({node.workflow_id for node in users})
            raise DestinationInUse(columns=len(users), sheets=sheets)
        with transaction.atomic():
            deleted, _ = WebhookDestination.objects.filter(id=destination.id, account_id=self.account_id).delete()
            if not deleted:
                return
            self.deliveries.delete_for(str(destination.id))

    def _webhook_nodes_naming(self, destination: WebhookDestination) -> list[object]:
        # Imported here: the lists app already depends on this one, and a
        # module-level import would make that a cycle.
        from lists.nodes.webhook import Webhook
        from lists.services.workflows import WorkflowService

        workflows = WorkflowService(account_id=self.account_id)
        return list(workflows.nodes_of_kind(Webhook.KIND, config__destination_id=str(destination.id)))

    def rotate(self, destination: WebhookDestination) -> tuple[WebhookDestination, str]:
        """A new signing secret, the old one kept to sign beside it for
        the grace window. Returns `(destination, raw_secret)`: the raw
        value exists only in this return and the response."""
        secret = mint_secret()
        destination.previous_signing_secret = destination.signing_secret
        destination.signing_secret = secret
        destination.rotated_at = timezone.now()
        destination.save(update_fields=["previous_signing_secret", "signing_secret", "rotated_at", "updated_at"])
        return destination, secret

    def signing_secrets_of(self, destination: WebhookDestination) -> list[str]:
        """The secrets a delivery signs with: the current one, and the
        retired one while its grace lasts."""
        secrets = [destination.signing_secret]
        if destination.previous_signing_secret and destination.rotated_at is not None:
            age = (timezone.now() - destination.rotated_at).total_seconds()
            if age < WEBHOOK_ROTATION_GRACE_SECONDS:
                secrets.append(destination.previous_signing_secret)
        return secrets

    def headers_of(self, destination: WebhookDestination) -> dict[str, str]:
        """The ONE decode of the encrypted column. A value that no
        longer parses (a rotated encryption key hands back ciphertext)
        raises json.JSONDecodeError for the caller to name."""
        decoded = json.loads(destination.headers or "{}")
        return {str(name): str(value) for name, value in decoded.items()}

    def header_names_of(self, destination: WebhookDestination) -> list[str]:
        """What the wire shows. Unreadable headers read as none, logged:
        a roster must render whatever one row holds."""
        try:
            return sorted(self.headers_of(destination))
        except json.JSONDecodeError:
            logger.warning("destination %s: headers unreadable", destination.id)
            return []

    def deliver(self, destination: WebhookDestination, *, test: bool, data: WebhookEnvelopeData) -> Sent:
        """One signed delivery of `data` to the destination, sent now
        whatever `enabled` says, then recorded. `enabled` gates the
        AUTOMATIC lane: a scheduled caller filters on it before calling
        here; an explicit Test does not. The
        one place the id, the type, the test flag, and the clock are
        written, so the wire and the log cannot disagree. The POST runs
        outside any transaction; the record lands after. Hands back the
        envelope beside the record, since nothing stores it."""
        envelope = envelope_of(test=test, data=data)
        delivery_id = envelope.id
        try:
            headers = self.headers_of(destination)
        except json.JSONDecodeError:
            logger.warning("destination %s: headers unreadable", destination.id)
            result = DeliveryResult(DeliveryStatus.BLOCKED, None, HEADERS_UNREADABLE)
        else:
            result = WebhookSender().send(
                url=destination.url,
                headers=headers,
                secrets=self.signing_secrets_of(destination),
                delivery_id=delivery_id,
                body=encode_body(envelope),
            )
        delivery = self.deliveries.record(
            str(destination.id),
            envelope_type=WebhookEnvelopeType(data.type),
            test=test,
            result=result,
            delivery_id=delivery_id,
        )
        return Sent(delivery=delivery, envelope=envelope)

    def test(self, destination: WebhookDestination) -> WebhookDelivery:
        """The destination page's Test button: a ping, nothing from a
        sheet, proving a signed delivery reaches the receiver."""
        ping = WebhookPingData(destination_id=str(destination.id), label=destination.label)
        sent = self.deliver(destination, test=True, data=ping)
        return sent.delivery

    def _guard_url(self, url: str) -> None:
        reason = destination_block_reason(
            url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            # An IP literal is refused on the spot; a hostname is not
            # resolved here, since DNS can change between now and send.
            resolve_dns=False,
        )
        if reason is not None:
            raise UrlBlocked(reason)

    def _guard_headers(self, headers: dict[str, str]) -> None:
        for name in headers:
            if name.lower() in RESERVED_WEBHOOK_HEADER_NAMES:
                raise HeaderReserved(name)

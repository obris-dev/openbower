"""Wire contract for outbound webhooks: the account's destinations, the
deliveries made to them, and the envelope a receiver gets."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .agents import LABEL_MAX_LENGTH

# Wire bounds live ON the contract (Field constraints below) so both
# sides enforce one number. Values are binary by house rule.
WEBHOOK_URL_MAX_LENGTH = 2048
WEBHOOK_HEADER_NAME_MAX_LENGTH = 128
WEBHOOK_HEADER_VALUE_MAX_LENGTH = 2048
# The head of a receiver's answer kept with a failed delivery, enough
# to read an error page's first line, never a body.
WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH = 1024

# Facts with no field to hang on (the web reads them as x-constants).
# The roster is unpaged, so a bound must exist; it counts every
# destination in the account.
MAX_WEBHOOK_DESTINATIONS = 32
MAX_WEBHOOK_HEADERS = 8
# The signature triple every delivery carries, and the shape of what
# rides in it: the Standard Webhooks scheme, so a receiver library
# verifies with the secret as shown.
WEBHOOK_ID_HEADER = "webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
WEBHOOK_SIGNATURE_HEADER = "webhook-signature"
WEBHOOK_SIGNATURE_VERSION = "v1"
WEBHOOK_SECRET_PREFIX = "whsec_"
# Header names the sender owns: the signature triple, the framing the
# transport sets, and the identity every delivery carries. A configured
# header under one of these is refused at write and dropped at send.
RESERVED_WEBHOOK_HEADER_NAMES: tuple[str, ...] = (
    "accept-encoding",
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "host",
    "transfer-encoding",
    "user-agent",
    WEBHOOK_ID_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
)
# A header name is a token; a value is visible ASCII plus tab. Anything
# else (a newline, a non-latin-1 character) makes the HTTP layer refuse
# the request on every send, so it refuses once, at write.
WEBHOOK_HEADER_NAME_GRAMMAR = r"^[A-Za-z0-9-]+$"
WEBHOOK_HEADER_VALUE_GRAMMAR = r"^[\x20-\x7E\t]*$"

# What a delivery carried: a test from the destination page, or a
# digest of completed rows (the flush). A receiver branches on the
# envelope's `type`, which names the same thing.
WebhookDeliveryKindWire = Literal["test", "digest"]
# How one attempt ended. `transient` means the receiver may accept a
# retry; `rejected` means the request itself was refused; `blocked`
# means this deployment refused to send (the address is not reachable
# from here, or the destination's signing secret is unreadable).
DeliveryStatusWire = Literal["ok", "transient", "rejected", "blocked"]


class WebhookDeliveryWire(BaseModel):
    """One attempt to POST to a destination, whatever it answered. `id`
    is the `webhook-id` header that request carried."""

    id: str
    destination_id: str
    kind: WebhookDeliveryKindWire
    status: DeliveryStatusWire
    http_status: int | None = Field(default=None, description="The receiver's status code; null when no answer came.")
    duration_ms: int = 0
    error: str = Field(default="", description="What went wrong, in the user's words; empty on ok.")
    response_excerpt: str = Field(
        default="",
        max_length=WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH,
        description="The head of the receiver's answer, kept only when it was not a 2xx.",
    )
    created_at: str


class WebhookDestinationWire(BaseModel):
    """A place deliveries go. Header VALUES and the signing secret never
    ride here; `header_names` is all the roster shows."""

    id: str
    label: str = Field(max_length=LABEL_MAX_LENGTH)
    url: str = Field(max_length=WEBHOOK_URL_MAX_LENGTH)
    # A literal default, not default_factory: only the literal reaches
    # the generated schema as a default.
    header_names: list[str] = []
    enabled: bool = True
    last_delivery: WebhookDeliveryWire | None = Field(
        default=None, description="The newest delivery on record, the destination's health; null before any."
    )
    created_at: str


class WebhookDestinationsList(BaseModel):
    items: list[WebhookDestinationWire]


class WebhookDestinationCreated(BaseModel):
    """The create response: the ONE time the signing secret is on the
    wire. It is minted server-side, stored encrypted, and never returned
    again; a lost secret means deleting the destination and adding it
    again."""

    destination: WebhookDestinationWire
    signing_secret: str


class WebhookDeliveriesPage(BaseModel):
    items: list[WebhookDeliveryWire]
    next_cursor: str | None = None


class WebhookEnvelope(BaseModel):
    """What a receiver gets, as the request body. Every delivery is a
    POST of this JSON with three headers: `webhook-id` (this `id`),
    `webhook-timestamp` (unix seconds), and `webhook-signature`
    (`v1,` then base64 of HMAC-SHA256 over `"{id}.{timestamp}.{body}"`,
    keyed by the base64-decoded secret after its `whsec_` prefix), the
    Standard Webhooks scheme. A retried delivery carries a NEW id; a
    digest's items each carry their own dedup key, which is what a
    receiver of batches deduplicates on."""

    id: str
    type: WebhookDeliveryKindWire
    version: int = 1
    timestamp: str
    data: dict[str, Any]

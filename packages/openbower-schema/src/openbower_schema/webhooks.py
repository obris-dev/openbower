"""Wire contract for outbound webhooks: the account's destinations, the
deliveries made to them, and the envelope a receiver gets."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .agents import LABEL_MAX_LENGTH
from .lists import WireCellState

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
# How often a webhook column's completed rows are batched and sent: a
# closed list of presets (seconds), so the drawer offers a choice rather
# than a number, and the flush's tick can never be asked for a value it
# would refuse.
WEBHOOK_CADENCE_SECONDS: tuple[int, ...] = (300, 900, 3600, 21_600, 86_400)
DEFAULT_WEBHOOK_CADENCE_SECONDS = 900
# After a secret rotation, deliveries carry a signature under the old
# secret too for this long (binary; above the 24 h the reference
# implementation keeps), so a receiver switches at its own pace.
WEBHOOK_ROTATION_GRACE_SECONDS = 131_072
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

# What an envelope's `data` IS: a ping from a destination's Test button,
# or a digest of completed rows. A receiver branches on it; the log
# records it beside `test`.
WebhookEnvelopeTypeWire = Literal["ping", "digest"]
# How one attempt ended. `transient` means the receiver may accept a
# retry; `rejected` means the request itself was refused; `blocked`
# means this deployment refused to send (the address is not reachable
# from here, or the destination's secret or headers are unreadable).
DeliveryStatusWire = Literal["ok", "transient", "rejected", "blocked"]


class WebhookPingData(BaseModel):
    """A destination's Test button: nothing from a sheet, just proof
    that a signed delivery reaches the receiver."""

    type: Literal["ping"] = "ping"
    destination_id: str
    label: str


class WebhookSheetRef(BaseModel):
    id: str
    label: str


class WebhookDigestItem(BaseModel):
    """One completed row of a digest."""

    event_id: str = Field(
        description="An opaque, fixed-width id for this row completion, the same on every redelivery "
        "of it; the same row completing again ships under a new one. Receivers dedup on it and never parse it."
    )
    row_id: str
    position: int
    completed_at: str | None = Field(
        default=None,
        description="When the last waited-on column settled. Null only on a test send of a row "
        "that has not completed; a real digest never sends null.",
    )
    cells: dict[str, str] = Field(
        description="The payload columns only, by key (the user chooses what leaves the instance), "
        "so a waited-on key may appear in `states` and not here."
    )
    states: dict[str, WireCellState] = Field(
        description="The waited-on columns' stored cell states, by key; a column not yet attempted is absent. "
        "Never `pending`: a digest reads stored truth, and a row still being worked on is not complete."
    )


class WebhookDigestData(BaseModel):
    """Rows that completed since the last delivery, for one sheet."""

    type: Literal["digest"] = "digest"
    sheet: WebhookSheetRef
    waited_on: list[str] = Field(
        description="The columns whose settling makes a row complete; the keys each item's states report on."
    )
    items: list[WebhookDigestItem]


WebhookEnvelopeData = WebhookPingData | WebhookDigestData


class WebhookEnvelope(BaseModel):
    """What a receiver gets, as the request body. Every delivery is a
    POST of this JSON with three headers: `webhook-id` (this `id`),
    `webhook-timestamp` (unix seconds), and `webhook-signature`
    (`v1,` then base64 of HMAC-SHA256 over `"{id}.{timestamp}.{body}"`,
    keyed by the base64-decoded secret after its `whsec_` prefix), the
    Standard Webhooks scheme.

    `type` says what `data` is. `test` says the delivery came from a
    Test button (a ping, or a sample digest sent from a sheet) and must
    not be acted on as live data; a digest the schedule sends carries
    false. The top-level keys are reserved; everything a user defines
    is nested under `data`. A retried delivery carries a NEW id; a
    digest's items each carry their own `event_id`, which is what a
    receiver of batches deduplicates on."""

    id: str
    type: WebhookEnvelopeTypeWire
    version: int = 1
    test: bool = False
    timestamp: str
    data: WebhookEnvelopeData = Field(discriminator="type")

    @model_validator(mode="after")
    def _type_matches_data(self) -> WebhookEnvelope:
        # The top-level type is the data's, restated for receivers; a
        # pair that disagrees is a construction bug, refused here.
        if self.type != self.data.type:
            raise ValueError("envelope type must match its data's type")
        return self


class WebhookDeliveryWire(BaseModel):
    """One attempt to POST to a destination, whatever it answered. `id`
    is the `webhook-id` header that request carried; `type` and `test`
    are the envelope's own, recorded on our side."""

    id: str
    destination_id: str
    type: WebhookEnvelopeTypeWire
    test: bool = False
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
    rotated_at: str | None = Field(
        default=None,
        description="When the signing secret was last rotated; the previous secret keeps signing for the grace window after it.",
    )
    column_count: int = Field(default=0, description="How many Send webhook columns send here; delete is refused while any do.")
    created_at: str


class WebhookDestinationsList(BaseModel):
    items: list[WebhookDestinationWire]


class WebhookDestinationCreated(BaseModel):
    """The create and rotate response: the ONE time a signing secret is
    on the wire. It is minted server-side, stored encrypted, and never
    returned again; a lost secret is replaced by rotating."""

    destination: WebhookDestinationWire
    signing_secret: str


class WebhookDeliveriesPage(BaseModel):
    items: list[WebhookDeliveryWire]
    next_cursor: str | None = None


class WebhookColumnTestResponse(BaseModel):
    """A Send webhook column's test send: the delivery as recorded, and
    the envelope exactly as the receiver got it, so the sheet can show
    the body a test produced rather than reconstruct it."""

    delivery: WebhookDeliveryWire
    envelope: WebhookEnvelope


class WebhookColumnPreviewResponse(BaseModel):
    """The envelope a test send of these choices would carry, rendered by
    the server and sent nowhere, so the sheet shows the truth before a
    send rather than a reconstruction."""

    envelope: WebhookEnvelope


class WebhookColumnConfigWire(BaseModel):
    """A Send webhook column as configured: what it waits for (derived
    back from the paths its wait node names, in sheet order), where it
    sends, what rides, how often, and whether it is running."""

    node_id: str
    destination_id: str
    destination_label: str
    wait_keys: list[str]
    payload_keys: list[str]
    interval_seconds: int
    enabled: bool = True

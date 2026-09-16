"""Bounds and vocabularies for outbound webhooks. Wire-visible bounds
live in the schema package and are re-exported here; INVENTED numeric
bounds are binary by house rule."""

from enum import StrEnum

from openbower_schema.agents import LABEL_MAX_LENGTH as LABEL_MAX_LENGTH
from openbower_schema.webhooks import MAX_WEBHOOK_DESTINATIONS as MAX_WEBHOOK_DESTINATIONS
from openbower_schema.webhooks import MAX_WEBHOOK_HEADERS as MAX_WEBHOOK_HEADERS
from openbower_schema.webhooks import RESERVED_WEBHOOK_HEADER_NAMES as RESERVED_WEBHOOK_HEADER_NAMES
from openbower_schema.webhooks import WEBHOOK_HEADER_NAME_GRAMMAR as WEBHOOK_HEADER_NAME_GRAMMAR
from openbower_schema.webhooks import WEBHOOK_HEADER_NAME_MAX_LENGTH as WEBHOOK_HEADER_NAME_MAX_LENGTH
from openbower_schema.webhooks import WEBHOOK_HEADER_VALUE_GRAMMAR as WEBHOOK_HEADER_VALUE_GRAMMAR
from openbower_schema.webhooks import WEBHOOK_HEADER_VALUE_MAX_LENGTH as WEBHOOK_HEADER_VALUE_MAX_LENGTH
from openbower_schema.webhooks import WEBHOOK_ID_HEADER as WEBHOOK_ID_HEADER
from openbower_schema.webhooks import WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH as WEBHOOK_RESPONSE_EXCERPT_MAX_LENGTH
from openbower_schema.webhooks import WEBHOOK_SECRET_PREFIX as WEBHOOK_SECRET_PREFIX
from openbower_schema.webhooks import WEBHOOK_SIGNATURE_HEADER as WEBHOOK_SIGNATURE_HEADER
from openbower_schema.webhooks import WEBHOOK_SIGNATURE_VERSION as WEBHOOK_SIGNATURE_VERSION
from openbower_schema.webhooks import WEBHOOK_TIMESTAMP_HEADER as WEBHOOK_TIMESTAMP_HEADER
from openbower_schema.webhooks import WEBHOOK_URL_MAX_LENGTH as WEBHOOK_URL_MAX_LENGTH

# The user-facing sentence kept with a failed delivery.
WEBHOOK_ERROR_MAX_LENGTH = 256
# Deliveries are a log, pruned by age (2^21 s, about 24 days): long
# enough to debug a receiver that failed over a weekend, short enough
# that the table never grows with the account.
WEBHOOK_DELIVERY_MAX_AGE_SECONDS = 2_097_152
# Deliveries page by keyset; the bound keeps one response small.
DEFAULT_DELIVERIES_PAGE = 32
MAX_DELIVERIES_PAGE = 128
# Ids deleted per prune transaction.
DELIVERY_WRITE_BATCH = 1000
# The signing secret is the contract's prefix and base64 of this many
# random bytes.
SIGNING_SECRET_BYTES = 24
# Connect and write phases of one delivery; the read phase is the
# WEBHOOK_TIMEOUT_SECONDS setting (a receiver that accepts and never
# answers is the slow case worth an operator knob).
WEBHOOK_CONNECT_TIMEOUT_SECONDS = 4
# The shared client's pool: how many connections one process holds
# open at once, and how many idle ones it keeps for reuse.
WEBHOOK_POOL_CONNECTIONS = 32
WEBHOOK_POOL_KEEPALIVE = 8
STATUS_MAX_LENGTH = 16
KIND_MAX_LENGTH = 16


class WebhookDeliveryKind(StrEnum):
    """What a delivery carried. The envelope's `type` names the same
    value, so a receiver branches on one vocabulary."""

    TEST = "test"
    DIGEST = "digest"


class DeliveryStatus(StrEnum):
    """How one attempt ended. TRANSIENT may succeed on a retry; REJECTED
    means the request was refused; BLOCKED means this deployment never
    sent (the address is unreachable from here, or the secret is
    unreadable)."""

    OK = "ok"
    TRANSIENT = "transient"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class WebhookErrorCode(StrEnum):
    """Refusal codes, all 400: each is fixed by the caller changing the
    request or the account (a full roster empties by deleting), never
    by waiting."""

    DESTINATIONS_FULL = "destinations_full"
    URL_BLOCKED = "url_blocked"
    HEADER_RESERVED = "header_reserved"

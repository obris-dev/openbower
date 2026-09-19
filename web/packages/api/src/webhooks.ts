// Webhooks: the account's destinations (where deliveries go), the test
// delivery, and each destination's delivery log. Same result-union
// philosophy as lists.

import {
  WebhookDeliveriesPageSchema,
  WebhookDeliveryWireSchema,
  WebhookDestinationCreatedSchema,
  WebhookDestinationsListSchema,
  WebhookDestinationWireSchema,
  WIRE_BOUNDS,
  WIRE_CONSTANTS,
  type WebhookDeliveriesPage,
  type WebhookDeliveryWire,
  type WebhookDestinationCreated,
  type WebhookDestinationsList,
  type WebhookDestinationWire,
} from "@bower/schema";

import { z } from "zod";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type {
  WebhookDeliveriesPage,
  WebhookDeliveryWire,
  WebhookDestinationCreated,
  WebhookDestinationsList,
  WebhookDestinationWire,
};

// Size bounds, straight off the contract document (the server's
// serializers enforce the same numbers; nothing here is invented).
export const WEBHOOK_LABEL_MAX_LENGTH = WIRE_BOUNDS.WebhookDestinationWire.label.maxLength;
export const WEBHOOK_URL_MAX_LENGTH = WIRE_BOUNDS.WebhookDestinationWire.url.maxLength;
// Header facts have no wire field (values never ride the wire), so they
// come off the constants block; the headers grammar leaf mirrors them.
export const MAX_WEBHOOK_DESTINATIONS = WIRE_CONSTANTS.MAX_WEBHOOK_DESTINATIONS;
export const MAX_WEBHOOK_HEADERS = WIRE_CONSTANTS.MAX_WEBHOOK_HEADERS;
export const WEBHOOK_HEADER_NAME_MAX_LENGTH = WIRE_CONSTANTS.WEBHOOK_HEADER_NAME_MAX_LENGTH;
export const WEBHOOK_HEADER_VALUE_MAX_LENGTH = WIRE_CONSTANTS.WEBHOOK_HEADER_VALUE_MAX_LENGTH;
export const WEBHOOK_HEADER_NAME_GRAMMAR = new RegExp(WIRE_CONSTANTS.WEBHOOK_HEADER_NAME_GRAMMAR);
export const WEBHOOK_HEADER_VALUE_GRAMMAR = new RegExp(WIRE_CONSTANTS.WEBHOOK_HEADER_VALUE_GRAMMAR);
export const RESERVED_WEBHOOK_HEADER_NAMES: readonly string[] = WIRE_CONSTANTS.RESERVED_WEBHOOK_HEADER_NAMES;
// The signature scheme's names, for the verify guide and the log: read
// off the contract, never retyped.
export const WEBHOOK_ID_HEADER = WIRE_CONSTANTS.WEBHOOK_ID_HEADER;
export const WEBHOOK_TIMESTAMP_HEADER = WIRE_CONSTANTS.WEBHOOK_TIMESTAMP_HEADER;
export const WEBHOOK_SIGNATURE_HEADER = WIRE_CONSTANTS.WEBHOOK_SIGNATURE_HEADER;
export const WEBHOOK_SIGNATURE_VERSION = WIRE_CONSTANTS.WEBHOOK_SIGNATURE_VERSION;
export const WEBHOOK_SECRET_PREFIX = WIRE_CONSTANTS.WEBHOOK_SECRET_PREFIX;

// The server's refusal codes (webhooks.constants.WebhookErrorCode), all
// 400s carrying the server's own detail; the drawer routes each to the
// surface it names.
export const DESTINATIONS_FULL_CODE = "destinations_full";
// 409 on DELETE while a webhook column sends here; rendered at the confirm tier.
export const DESTINATION_IN_USE_CODE = "destination_in_use";
// A second rotation inside the grace window: a 409 the confirm tier
// renders in the server's words, since the fix is the window closing.
export const ROTATION_IN_PROGRESS_CODE = "rotation_in_progress";
// After a rotation the old secret keeps signing for this long.
export const WEBHOOK_ROTATION_GRACE_SECONDS: number = WIRE_CONSTANTS.WEBHOOK_ROTATION_GRACE_SECONDS;
export const WEBHOOK_URL_BLOCKED_CODE = "url_blocked";
export const WEBHOOK_HEADER_RESERVED_CODE = "header_reserved";

export type WebhookHeader = { name: string; value: string };
export type DestinationBody = { label: string; url: string; headers: WebhookHeader[] };
export type DestinationPatchBody = { label?: string; url?: string; enabled?: boolean; headers?: WebhookHeader[] };

// A delivery's status and type are the SERVER's enums, and this bundle
// can predate the next contract: strict-parsing them would fail the
// whole settings page for every open tab. Widen the read and map an
// unknown member to a CLIENT member, not to one of the server's: a real
// status renders as a claim ("Failed"), and a status this bundle cannot
// name is not one it can make (the lists client's UNKNOWN_CELL_STATE
// idiom). `test` is a plain boolean on every delivery.
export const UNKNOWN_DELIVERY_STATUS = "unknown" as const;
export const UNKNOWN_DELIVERY_TYPE = "unknown" as const;
export type RenderableDeliveryStatus = WebhookDeliveryWire["status"] | typeof UNKNOWN_DELIVERY_STATUS;
export type RenderableDeliveryType = WebhookDeliveryWire["type"] | typeof UNKNOWN_DELIVERY_TYPE;
/** A delivery as the app renders it: the wire's shape with the two
 * server-owned enums widened by their client-only unknown member. */
export type RenderableDelivery = Omit<WebhookDeliveryWire, "status" | "type"> & {
  status: RenderableDeliveryStatus;
  type: RenderableDeliveryType;
};
export type RenderableDestination = Omit<WebhookDestinationWire, "last_delivery"> & {
  last_delivery: RenderableDelivery | null;
};
export type RenderableDeliveriesPage = Omit<WebhookDeliveriesPage, "items"> & { items: RenderableDelivery[] };
export type RenderableDestinationCreated = Omit<WebhookDestinationCreated, "destination"> & {
  destination: RenderableDestination;
};

const STATUSES = new Set<string>(WebhookDeliveryWireSchema.shape.status.options);
const TYPES = new Set<string>(WebhookDeliveryWireSchema.shape.type.options);
export const TolerantWebhookDeliveryWireSchema = WebhookDeliveryWireSchema.extend({
  status: z.string(),
  type: z.string(),
}).transform(
  (raw): RenderableDelivery => ({
    ...raw,
    status: (STATUSES.has(raw.status) ? raw.status : UNKNOWN_DELIVERY_STATUS) as RenderableDeliveryStatus,
    type: (TYPES.has(raw.type) ? raw.type : UNKNOWN_DELIVERY_TYPE) as RenderableDeliveryType,
  }),
);
export const TolerantWebhookDestinationWireSchema = WebhookDestinationWireSchema.extend({
  last_delivery: TolerantWebhookDeliveryWireSchema.nullable().default(null),
});
export const TolerantWebhookDestinationsListSchema = WebhookDestinationsListSchema.extend({
  items: z.array(TolerantWebhookDestinationWireSchema),
});
export const TolerantWebhookDestinationCreatedSchema = WebhookDestinationCreatedSchema.extend({
  destination: TolerantWebhookDestinationWireSchema,
});
export const TolerantWebhookDeliveriesPageSchema = WebhookDeliveriesPageSchema.extend({
  items: z.array(TolerantWebhookDeliveryWireSchema),
});

export async function fetchWebhooks(): Promise<ApiResult<{ items: RenderableDestination[] }>> {
  return http.get(apiRoutes.webhooks.index, TolerantWebhookDestinationsListSchema);
}

/** Create a destination. The response carries the signing secret ONCE;
 * no later read returns it. */
export async function createWebhook(body: DestinationBody): Promise<ApiResult<RenderableDestinationCreated>> {
  return http.post(apiRoutes.webhooks.index, TolerantWebhookDestinationCreatedSchema, body);
}

export async function fetchWebhook(id: string): Promise<ApiResult<RenderableDestination>> {
  return http.get(apiRoutes.webhooks.detail(id), TolerantWebhookDestinationWireSchema);
}

/** Any subset; `headers` present replaces the whole set. */
export async function updateWebhook(id: string, patch: DestinationPatchBody): Promise<ApiResult<RenderableDestination>> {
  return http.patch(apiRoutes.webhooks.detail(id), TolerantWebhookDestinationWireSchema, patch);
}

export async function deleteWebhook(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.webhooks.detail(id));
}

/** A new signing secret, shown ONCE in the create response's shape;
 * the old one keeps signing for the grace window. */
export async function rotateWebhook(id: string): Promise<ApiResult<RenderableDestinationCreated>> {
  return http.post(apiRoutes.webhooks.rotate(id), TolerantWebhookDestinationCreatedSchema, {});
}

/** One signed test ping, sent now. Answers ok with the delivery
 * whatever the receiver did: its `status` is the outcome. */
export async function testWebhook(id: string): Promise<ApiResult<RenderableDelivery>> {
  return http.post(apiRoutes.webhooks.test(id), TolerantWebhookDeliveryWireSchema, {});
}

/** The delivery log, newest first, keyset by `after`. */
export async function fetchWebhookDeliveries(id: string, after?: string): Promise<ApiResult<RenderableDeliveriesPage>> {
  const query = after ? `?after=${encodeURIComponent(after)}` : "";
  return http.get(`${apiRoutes.webhooks.deliveries(id)}${query}`, TolerantWebhookDeliveriesPageSchema);
}

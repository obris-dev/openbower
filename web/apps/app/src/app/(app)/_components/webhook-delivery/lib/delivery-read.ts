import type { RenderableDelivery, RenderableDestination } from "@bower/api";

import { formatTime } from "./format-time.ts";

export type DeliveryTone = "muted" | "warning" | "danger";
export type DeliveryRead = { tone: DeliveryTone; line: string };

// One tone-to-role map for every surface that renders a delivery
// (roles, never palette classes).
export const TONE_CLASS: Record<DeliveryTone, string> = {
  muted: "text-muted",
  warning: "text-warning",
  danger: "text-danger",
};

type Facts = Pick<RenderableDestination, "enabled" | "last_delivery">;

// The word each status renders as, with its tone. The tone derives
// from `status`, never from the error text. An unknown status (a
// member this bundle predates) is on record but claims nothing.
const WORD: Record<RenderableDelivery["status"], DeliveryRead> = {
  ok: { tone: "muted", line: "Delivered" },
  transient: { tone: "danger", line: "Failed" },
  rejected: { tone: "danger", line: "Refused" },
  blocked: { tone: "danger", line: "Blocked" },
  unknown: { tone: "muted", line: "Recorded" },
};

// What the delivery carried, by the envelope's own two facts: the
// shape, and whether a Test button sent it. A shape this bundle cannot
// name gets no word (naming it would claim what it carried).
const TYPE_WORD: Record<RenderableDelivery["type"], string> = {
  ping: "ping",
  digest: "digest",
  unknown: "",
};

/** The one status line a destination card shows, derived from the
 * wire's facts (never a server-shipped sentence): paused, the newest
 * delivery's outcome and time, or not yet. The verbatim `error` renders
 * beside it on the card; this line names the state. */
export function deliveryRead(destination: Facts): DeliveryRead {
  if (!destination.enabled) return { tone: "warning", line: "Paused" };
  const last = destination.last_delivery;
  if (!last) return { tone: "muted", line: "No deliveries yet" };
  const word = WORD[last.status];
  const http = last.http_status === null ? "" : ` | ${last.http_status}`;
  return { tone: word.tone, line: `${word.line} ${formatTime(last.created_at)}${http}` };
}

/** The status word for one log row, with its tone. */
export function deliveryWord(delivery: Pick<RenderableDelivery, "status">): DeliveryRead {
  return WORD[delivery.status];
}

/** "Test digest", "Test ping", "Digest": the envelope's shape and its
 * test flag, as a label; "Test" alone for a test of an unknown shape,
 * "" for an unknown live one. */
export function deliveryLabel(delivery: Pick<RenderableDelivery, "type" | "test">): string {
  const shape = TYPE_WORD[delivery.type];
  if (delivery.test) return shape ? `Test ${shape}` : "Test";
  return shape ? shape.charAt(0).toUpperCase() + shape.slice(1) : "";
}

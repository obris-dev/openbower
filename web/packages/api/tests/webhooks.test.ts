import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_WEBHOOK_HEADERS,
  RESERVED_WEBHOOK_HEADER_NAMES,
  TolerantWebhookDestinationWireSchema,
  UNKNOWN_DELIVERY_KIND,
  UNKNOWN_DELIVERY_STATUS,
  WEBHOOK_HEADER_NAME_GRAMMAR,
  WEBHOOK_HEADER_VALUE_GRAMMAR,
} from "../src/webhooks.ts";

const DELIVERY = {
  id: "01DLV",
  destination_id: "01DST",
  kind: "test",
  status: "ok",
  http_status: 200,
  duration_ms: 12,
  error: "",
  response_excerpt: "",
  created_at: "2026-09-16T12:00:00+00:00",
};

const DESTINATION = {
  id: "01DST",
  label: "CRM sync",
  url: "https://hooks.example.com/in",
  header_names: ["Authorization"],
  enabled: true,
  last_delivery: DELIVERY,
  created_at: "2026-09-16T12:00:00+00:00",
};

test("an unknown delivery status or kind parses and reads as a client-only unknown", () => {
  // The tolerant half: the WHOLE destination must survive a member this
  // bundle predates (phase 4 adds a kind; a strict enum would fail the
  // parse and blank the settings page). The mapping half: an unknown
  // reads as a CLIENT member, never as a real status, because a real
  // status is rendered as a claim ("Failed") the bundle cannot make.
  const parsed = TolerantWebhookDestinationWireSchema.parse({
    ...DESTINATION,
    last_delivery: { ...DELIVERY, status: "quarantined", kind: "digest_v2" },
  });
  assert.equal(parsed.last_delivery?.status, UNKNOWN_DELIVERY_STATUS);
  assert.equal(parsed.last_delivery?.kind, UNKNOWN_DELIVERY_KIND);
  // Known members pass through typed; an absent delivery reads as null.
  assert.equal(TolerantWebhookDestinationWireSchema.parse(DESTINATION).last_delivery?.status, "ok");
  assert.equal(TolerantWebhookDestinationWireSchema.parse({ ...DESTINATION, last_delivery: null }).last_delivery, null);
});

test("the header grammar and the reserved set come off the contract, never hand-retyped", () => {
  // The client mirrors the server's serializer so a shape 400 (which
  // carries no user copy) is unreachable from the editor.
  assert.equal(WEBHOOK_HEADER_NAME_GRAMMAR.test("X-Api-Key"), true);
  assert.equal(WEBHOOK_HEADER_NAME_GRAMMAR.test("bad name"), false);
  assert.equal(WEBHOOK_HEADER_VALUE_GRAMMAR.test("Bearer abc.def-123"), true);
  assert.equal(WEBHOOK_HEADER_VALUE_GRAMMAR.test("a\nb"), false);
  assert.equal(WEBHOOK_HEADER_VALUE_GRAMMAR.test("café"), false);
  assert.ok(RESERVED_WEBHOOK_HEADER_NAMES.includes("webhook-signature"));
  assert.ok(RESERVED_WEBHOOK_HEADER_NAMES.includes("content-type"));
  assert.ok(MAX_WEBHOOK_HEADERS > 0);
});

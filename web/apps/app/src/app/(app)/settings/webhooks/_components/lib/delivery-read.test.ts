import assert from "node:assert/strict";
import { test } from "node:test";

import { deliveryRead, deliveryWord } from "./delivery-read.ts";

const DELIVERY = {
  id: "01DLV",
  destination_id: "01DST",
  kind: "test" as const,
  status: "ok" as const,
  http_status: 200,
  duration_ms: 12,
  error: "",
  response_excerpt: "",
  created_at: "2026-09-16T17:01:12+00:00",
};

test("a paused destination reads paused before anything else", () => {
  assert.deepEqual(deliveryRead({ enabled: false, last_delivery: DELIVERY }), { tone: "warning", line: "Paused" });
});

test("no delivery yet is a quiet line, not a failure", () => {
  assert.deepEqual(deliveryRead({ enabled: true, last_delivery: null }), { tone: "muted", line: "No deliveries yet" });
});

test("the newest delivery's status and time make the line; the tone comes from status, not copy", () => {
  assert.deepEqual(deliveryRead({ enabled: true, last_delivery: DELIVERY }), {
    tone: "muted",
    line: "Delivered Sep 16, 17:01:12 UTC | 200",
  });
  assert.deepEqual(
    deliveryRead({ enabled: true, last_delivery: { ...DELIVERY, status: "transient", http_status: 503, error: "x" } }),
    { tone: "danger", line: "Failed Sep 16, 17:01:12 UTC | 503" },
  );
  assert.deepEqual(deliveryRead({ enabled: true, last_delivery: { ...DELIVERY, status: "blocked", http_status: null } }), {
    tone: "danger",
    line: "Blocked Sep 16, 17:01:12 UTC",
  });
  assert.deepEqual(deliveryWord({ status: "rejected" }), { tone: "danger", line: "Refused" });
});

test("a status this bundle cannot name renders as recorded, never as a failure", () => {
  // The client-only member the tolerant read maps unknowns to: the
  // delivery happened and is on record; what it meant is not ours to say.
  assert.deepEqual(deliveryWord({ status: "unknown" }), { tone: "muted", line: "Recorded" });
  assert.deepEqual(deliveryRead({ enabled: true, last_delivery: { ...DELIVERY, status: "unknown", kind: "unknown" } }), {
    tone: "muted",
    line: "Recorded Sep 16, 17:01:12 UTC | 200",
  });
});

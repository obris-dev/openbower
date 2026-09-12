import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchFillRun } from "../src/fills.ts";

const DETAIL_ENVELOPE = {
  agent_id: "",
  column_keys: ["answer"],
  confirmed_row_count: 1,
  counters: {
    attempted: 0,
    blank: 0,
    filled: 0,
    transient: 0,
  },
  created_at: "2026-01-01T00:00:00Z",
  id: "01CCCCCCCCCCCCCCCCCCCCCCCC",
  kind: "test",
  list_id: "",
  started_by: "01DDDDDDDDDDDDDDDDDDDDDDDD",
  status: "running",
  updated_at: "2026-01-01T00:00:00Z",
};

function stubFetch(t: { after: (fn: () => void) => void }, body: unknown) {
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;
}

test("an unknown run status reads as running, the value that promises least", async (t) => {
  // web/AGENTS.md: a read whose enum the SERVER owns is tolerant, and
  // the mapping is PINNED. A strict parse turned one added status
  // member into a permanent parse failure, which the bench poll reads
  // as a blip: the loop backed off forever with the Test button stuck
  // busy. RUNNING keeps the loop alive and claims nothing terminal.
  stubFetch(t, { ...DETAIL_ENVELOPE, status: "paused" });
  const res = await fetchFillRun(DETAIL_ENVELOPE.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, "running");
});

test("a known terminal status is passed through untouched", async (t) => {
  // The tolerance must not flatten every status to running: only a
  // member this bundle has never heard of maps, or the loop would
  // never terminate.
  stubFetch(t, { ...DETAIL_ENVELOPE, status: "complete" });
  const res = await fetchFillRun(DETAIL_ENVELOPE.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, "complete");
});

test("an unknown kind maps to test, the only kind this namespace serves", async (t) => {
  stubFetch(t, { ...DETAIL_ENVELOPE, kind: "example" });
  const res = await fetchFillRun(DETAIL_ENVELOPE.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.kind, "test");
});

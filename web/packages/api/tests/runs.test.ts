import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchRun, isRunOpen, UNKNOWN_RUN_STATUS } from "../src/runs.ts";

const RUN = {
  created_at: "2026-01-01T00:00:00Z",
  heartbeat_at: "2026-01-01T00:00:00Z",
  id: "01CCCCCCCCCCCCCCCCCCCCCCCC",
  result: null,
  status: "processing",
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

test("an unknown run status reads as the client's unknown member and stays open", async (t) => {
  // web/AGENTS.md: a read whose enum the SERVER owns is tolerant, and
  // the mapping is PINNED. A strict parse turned one added status
  // member into a permanent parse failure, which the preview poll reads
  // as a blip: the loop backed off forever with the Test button stuck
  // busy. The unknown member claims nothing terminal (the loop stays
  // open) but is a CLIENT member, so the poll can count it toward its
  // error bound instead of polling an unheard-of terminal forever.
  stubFetch(t, { ...RUN, status: "paused" });
  const res = await fetchRun(RUN.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, UNKNOWN_RUN_STATUS);
  assert.equal(isRunOpen(res.data), true);
});

test("a known terminal status is passed through untouched and reads closed", async (t) => {
  // The tolerance must not flatten every status to processing: only a
  // member this bundle has never heard of maps, or the loop would
  // never terminate.
  stubFetch(t, { ...RUN, status: "done" });
  const res = await fetchRun(RUN.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, "done");
  assert.equal(isRunOpen(res.data), false);
});

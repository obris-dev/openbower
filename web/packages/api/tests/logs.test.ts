// The signals list: the page parse, the cursor, and the TOLERANT status
// (a status the server adds must not fail every open tab's parse), held
// to the contract's own enum so a new one fails here until it is named.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { LogSummarySchema } from "@bower/schema";

import { LOG_STATUS, UNKNOWN_LOG_STATUS, deleteLog, fetchLogsPage } from "../src/index.ts";

const SHEET = {
  id: "01JQ" + "S".repeat(22),
  label: "Seed raises",
  kind: "log",
  origin: "manual",
  origin_ref: "",
  folder_id: "",
  row_count: 3,
  columns: [],
  created_at: "2026-11-12T12:00:00+00:00",
  updated_at: "2026-11-12T12:00:00+00:00",
};

function log(status: string) {
  return {
    sheet: SHEET,
    listener_id: "01JQ" + "L".repeat(22),
    signal: "funding",
    status,
    last_polled_at: "2026-11-12T12:01:00+00:00",
    autofill: true,
  };
}

function stubFetch(t: { after: (fn: () => void) => void }, body: unknown, status = 200) {
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return calls;
}

test("a page of signals parses, and the cursor rides ?after= encoded", async (t) => {
  const calls = stubFetch(t, { items: [log("active")], next_cursor: null });
  const res = await fetchLogsPage("01JQ/next");
  assert.equal(res.status, "ok");
  assert.ok(calls[0]?.url.endsWith("/v1/logs?after=01JQ%2Fnext"));
  if (res.status === "ok") {
    assert.equal(res.data.items[0]?.signal, "funding");
    assert.equal(res.data.items[0]?.sheet.kind, "log");
  }
});

test("every known status passes through untouched", async (t) => {
  for (const status of Object.values(LOG_STATUS)) {
    stubFetch(t, { items: [log(status)], next_cursor: null });
    const res = await fetchLogsPage();
    assert.equal(res.status === "ok" && res.data.items[0]?.status, status);
  }
});

test("an unknown status reads as the client-only member, which claims nothing", async (t) => {
  // FAILS if it maps to a real status: active would claim the signal
  // fills, and every other one tells a person to act.
  stubFetch(t, { items: [log("paused_for_review")], next_cursor: null });
  const res = await fetchLogsPage();
  assert.equal(res.status === "ok" && res.data.items[0]?.status, UNKNOWN_LOG_STATUS);
});

test("LOG_STATUS names exactly the contract's statuses", () => {
  // FAILS when the server adds or drops one: the web names it here first.
  assert.deepEqual(new Set(Object.values(LOG_STATUS)), new Set(LogSummarySchema.shape.status.options));
});

test("deleting a signal sends DELETE to its log", async (t) => {
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(null, { status: 204 });
  }) as typeof fetch;
  const res = await deleteLog(SHEET.id);
  assert.equal(res.status, "ok");
  assert.equal(calls[0]?.init.method, "DELETE");
  assert.ok(calls[0]?.url.endsWith(`/v1/logs/${SHEET.id}`));
});

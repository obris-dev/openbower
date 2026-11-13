// The signals list: the page parse, the cursor, and the TOLERANT status
// (a status the server adds must not fail every open tab's parse), held
// to the contract's own enum so a new one fails here until it is named.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { ListenerWireSchema, LogSummarySchema } from "@bower/schema";

import {
  LISTENER_STATUS,
  LOG_STATUS,
  UNKNOWN_LISTENER_STATUS,
  UNKNOWN_LOG_STATUS,
  deleteLog,
  fetchLog,
  fetchLogsPage,
  reconnectLogs,
} from "../src/index.ts";

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

function detail(logStatus: string, listener: Record<string, unknown> | null) {
  return { sheet: SHEET, listener, signal: "funding", status: logStatus, last_polled_at: null, autofill: true };
}

function listener(status: string) {
  return {
    id: "01JQ" + "L".repeat(22),
    label: "Seed raises",
    signal: "funding",
    user_id: "u1",
    status,
    error: null,
    version: {
      id: "v1",
      filter: { stages: ["seed"], min_amount: null, max_amount: null, currency: null },
      prompt: "",
      min_confidence: 0.8,
      user_id: "u1",
      created_at: "2026-11-12T12:00:00+00:00",
    },
    created_at: "2026-11-12T12:00:00+00:00",
    updated_at: "2026-11-12T12:00:00+00:00",
  };
}

test("one signal reads with its listener, and a gone listener reads as null", async (t) => {
  const calls = stubFetch(t, detail("active", listener("held")));
  const res = await fetchLog(SHEET.id);
  assert.ok(calls[0]?.url.endsWith(`/v1/logs/${SHEET.id}`));
  assert.equal(res.status === "ok" && res.data.listener?.status, LISTENER_STATUS.HELD);
  stubFetch(t, detail("listener_gone", null));
  const gone = await fetchLog(SHEET.id);
  assert.equal(gone.status === "ok" && gone.data.listener, null);
});

test("an unknown listener state, error kind or stage never fails the signal", async (t) => {
  // FAILS if any of the listener's server-owned sets is read strictly: the
  // signal's sheet would lose its status and banner on an added value.
  const later = { ...listener("paused_for_review"), signal: "hiring" };
  later.error = { kind: "a_kind_added_later" } as unknown as null;
  later.version.filter.stages = ["seed", "series_z"];
  stubFetch(t, detail("rebalancing", later));
  const res = await fetchLog(SHEET.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, UNKNOWN_LOG_STATUS);
  assert.equal(res.data.listener?.status, UNKNOWN_LISTENER_STATUS);
  assert.equal(res.data.listener?.error?.kind, "unexpected");
  assert.deepEqual(res.data.listener?.version.filter.stages, ["seed"]);
  // FAILS if the added stage is dropped: the listener still keeps it.
  assert.deepEqual(res.data.listener?.version.filter.unknown_stages, ["series_z"]);
});

test("LISTENER_STATUS names exactly the contract's", () => {
  assert.deepEqual(new Set(Object.values(LISTENER_STATUS)), new Set(ListenerWireSchema.shape.status.options));
});

test("reconnect posts to the account's reconnect and reads which key was left live", async (t) => {
  const calls = stubFetch(t, { left_live_key_id: "k1", left_live_user_id: "u2" });
  const res = await reconnectLogs();
  assert.equal(calls[0]?.init.method, "POST");
  assert.ok(calls[0]?.url.endsWith("/v1/logs/reconnect"));
  assert.equal(res.status === "ok" && res.data.left_live_user_id, "u2");
});

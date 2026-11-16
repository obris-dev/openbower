// The signals list: the page parse, the cursor, and the TOLERANT status
// (a status the server adds must not fail every open tab's parse), held
// to the contract's own enum so a new one fails here until it is named.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { ListenerWireSchema, SignalSummarySchema } from "@bower/schema";
import { z } from "zod";

import {
  LISTENER_STATUS,
  SIGNAL_STATUS,
  SOURCE_KIND,
  UNKNOWN_LISTENER_STATUS,
  UNKNOWN_SIGNAL_STATUS,
  UNKNOWN_SOURCE_KIND,
  deleteSignal,
  hostedSource,
  SIGNAL_FEED,
  signalFeed,
  editSignal,
  fetchSignal,
  fetchSignalsPage,
  reconnectSignals,
} from "../src/index.ts";

const SHEET = {
  id: "01JQ" + "S".repeat(22),
  label: "Seed raises",
  kind: "log",
  origin: "manual",
  origin_ref: "",
  folder_id: "",
  row_count: 3,
  autofill: true,
  columns: [],
  created_at: "2026-11-12T12:00:00+00:00",
  updated_at: "2026-11-12T12:00:00+00:00",
};

const HOSTED = { kind: "hosted", signal: "funding", listener_id: "01JQ" + "L".repeat(22) };

function log(status: string) {
  return {
    sheet: SHEET,
    sources: [HOSTED],
    status,
    last_polled_at: "2026-11-12T12:01:00+00:00",
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
  const res = await fetchSignalsPage("01JQ/next");
  assert.equal(res.status, "ok");
  assert.ok(calls[0]?.url.endsWith("/v1/signals?after=01JQ%2Fnext"));
  if (res.status === "ok") {
    assert.equal(hostedSource(res.data.items[0]!)?.signal, "funding");
    assert.equal(res.data.items[0]?.sheet.kind, "log");
  }
});

test("every known status passes through untouched", async (t) => {
  for (const status of Object.values(SIGNAL_STATUS)) {
    stubFetch(t, { items: [log(status)], next_cursor: null });
    const res = await fetchSignalsPage();
    assert.equal(res.status === "ok" && res.data.items[0]?.status, status);
  }
});

test("an unknown status reads as the client-only member, which claims nothing", async (t) => {
  // FAILS if it maps to a real status: active would claim the signal
  // fills, and every other one tells a person to act.
  stubFetch(t, { items: [log("paused_for_review")], next_cursor: null });
  const res = await fetchSignalsPage();
  assert.equal(res.status === "ok" && res.data.items[0]?.status, UNKNOWN_SIGNAL_STATUS);
});

test("a hosted source reads with its own signal", async (t) => {
  stubFetch(t, { items: [log("active")], next_cursor: null });
  const res = await fetchSignalsPage();
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data.items[0]?.sources, [{ kind: SOURCE_KIND.HOSTED, signal: "funding" }]);
});

test("a custom signal reads as its webhook, waiting until a push arrives", async (t) => {
  const custom = { ...log("waiting"), sources: [{ kind: "webhook" }], last_polled_at: null };
  stubFetch(t, { items: [custom], next_cursor: null });
  const res = await fetchSignalsPage();
  assert.equal(res.status, "ok");
  if (res.status === "ok") {
    const item = res.data.items[0]!;
    assert.deepEqual([item.sources, item.status, hostedSource(item)], [[{ kind: SOURCE_KIND.WEBHOOK }], SIGNAL_STATUS.WAITING, undefined]);
  }
});

test("a source kind this bundle has not heard of claims nothing", async (t) => {
  stubFetch(t, { items: [{ ...log("active"), sources: [{ kind: "carrier_pigeon", wings: 2 }] }], next_cursor: null });
  const res = await fetchSignalsPage();
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data.items[0]?.sources, [{ kind: UNKNOWN_SOURCE_KIND }]);
});

test("a hosted source missing its own facts fails the parse", async (t) => {
  // FAILS if a malformed entry of a known kind reads as unknown: a broken
  // server would read as a signal this bundle cannot name, not as broken.
  stubFetch(t, { items: [{ ...log("active"), sources: [{ kind: "hosted" }] }], next_cursor: null });
  const res = await fetchSignalsPage();
  assert.notEqual(res.status, "ok");
});

test("SOURCE_KIND names exactly the contract's kinds", () => {
  // FAILS when the server adds or drops a kind: the web names it here first.
  const raw: z.ZodType = SignalSummarySchema.shape.sources.element;
  const element = raw instanceof z.ZodLazy ? raw.unwrap() : raw;
  const members = (element instanceof z.ZodUnion ? element.options : [element]) as z.ZodObject<{
    kind: z.ZodLiteral<string>;
  }>[];
  const kinds = new Set(members.map((member) => member.shape.kind.value));
  assert.deepEqual(new Set(Object.values(SOURCE_KIND)), kinds);
});

test("SIGNAL_STATUS names exactly the contract's statuses", () => {
  // FAILS when the server adds or drops one: the web names it here first.
  assert.deepEqual(new Set(Object.values(SIGNAL_STATUS)), new Set(SignalSummarySchema.shape.status.options));
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
  const res = await deleteSignal(SHEET.id);
  assert.equal(res.status, "ok");
  assert.equal(calls[0]?.init.method, "DELETE");
  assert.ok(calls[0]?.url.endsWith(`/v1/signals/${SHEET.id}`));
});

test("an edit PATCHes the signal with the body given, unknown stages and all", async (t) => {
  // FAILS if the client reshapes the body: a stage this bundle does not
  // know would be dropped on its way back, narrowing the saved setup.
  const calls = stubFetch(t, detail("active", listener("active")));
  const body = { label: "Seed raises", filter: { stages: ["seed", "series_z"] }, prompt: "", min_confidence: 0.75 };
  const res = await editSignal(SHEET.id, body);
  assert.equal(res.status, "ok");
  assert.equal(calls[0]?.init.method, "PATCH");
  assert.ok(calls[0]?.url.endsWith(`/v1/signals/${SHEET.id}`));
  assert.deepEqual(JSON.parse(String(calls[0]?.init.body)), body);
});

function detail(logStatus: string, listener: Record<string, unknown> | null) {
  return { sheet: SHEET, sources: [HOSTED], listener, status: logStatus, last_polled_at: null };
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
  const res = await fetchSignal(SHEET.id);
  assert.ok(calls[0]?.url.endsWith(`/v1/signals/${SHEET.id}`));
  assert.equal(res.status === "ok" && res.data.listener?.status, LISTENER_STATUS.HELD);
  stubFetch(t, detail("listener_gone", null));
  const gone = await fetchSignal(SHEET.id);
  assert.equal(gone.status === "ok" && gone.data.listener, null);
});

test("a custom signal reads with its webhook and no listener", async (t) => {
  // The shape a custom signal's settings page receives.
  stubFetch(t, { ...detail("waiting", null), sources: [{ kind: "webhook" }] });
  const res = await fetchSignal(SHEET.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.deepEqual([res.data.sources, res.data.listener, signalFeed(res.data)], [[{ kind: "webhook" }], null, SIGNAL_FEED.CUSTOM]);
});

test("an unknown listener state, error kind or stage never fails the signal", async (t) => {
  // FAILS if any of the listener's server-owned sets is read strictly: the
  // signal's sheet would lose its status and banner on an added value.
  const later = { ...listener("paused_for_review"), signal: "hiring" };
  later.error = { kind: "a_kind_added_later" } as unknown as null;
  later.version.filter.stages = ["seed", "series_z"];
  stubFetch(t, detail("rebalancing", later));
  const res = await fetchSignal(SHEET.id);
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.status, UNKNOWN_SIGNAL_STATUS);
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
  const res = await reconnectSignals();
  assert.equal(calls[0]?.init.method, "POST");
  assert.ok(calls[0]?.url.endsWith("/v1/signals/reconnect"));
  assert.equal(res.status === "ok" && res.data.left_live_user_id, "u2");
});

test("a signal's feed is hosted, custom, or one this bundle cannot name", () => {
  // FAILS if a signal with no hosted source reads as custom: a kind this
  // bundle has not heard of would claim rows are pushed to it.
  const of = (sources: { kind: string; signal?: string }[]) => signalFeed({ sources } as Parameters<typeof signalFeed>[0]);
  assert.deepEqual(
    [of([{ kind: "hosted", signal: "funding" }]), of([{ kind: "webhook" }]), of([{ kind: "unknown_kind" }])],
    [SIGNAL_FEED.HOSTED, SIGNAL_FEED.CUSTOM, SIGNAL_FEED.UNKNOWN],
  );
});

// addListRows: pinned here because no shipped UI posts rows yet; the
// contract (route, body shape, RowsAdded parse) must not rot silently.
// postFillRefill: its optional rows scope is pinned because omission
// vs presence is the contract (no body means all remaining rows).
import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  CELL_STATES,
  SETTLED_CELL_STATES,
  UNKNOWN_CELL_STATE,
  addListRows,
  fetchListRows,
  getFills,
  postFillRefill,
} from "../src/lists.ts";

test("addListRows posts rows and parses the RowsAdded receipt", async (t) => {
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ added: 2, row_count: 7 }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const res = await addListRows("01AAAAAAAAAAAAAAAAAAAAAAAA", [{ a: "1" }, { a: "2" }]);
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data, { added: 2, row_count: 7 });
  assert.equal(calls.length, 1);
  assert.ok(calls[0]!.url.endsWith("/v1/lists/01AAAAAAAAAAAAAAAAAAAAAAAA/rows"));
  assert.equal(calls[0]!.init.method, "POST");
  assert.equal(calls[0]!.init.credentials, "include");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), { rows: [{ a: "1" }, { a: "2" }] });
});

// A syntactically valid job envelope (values are fixtures, not
// meaning): the refill test only cares that the 201 parses.
const JOB_ENVELOPE = {
  agent_id: "01BBBBBBBBBBBBBBBBBBBBBBBB",
  column_keys: ["answer"],
  config_snapshot: {
    model: "acme-large",
    outputs: [{ key: "answer", label: "Answer", type: "text" }],
    prompt: "What is {{domain}}?",
    provider: "openai_compatible",
    source: "main",
    tools: {},
  },
  confirmed_row_count: 10,
  counters: {
    attempted: 0,
    blank: 0,
    concurrency_point: 0,
    filled: 0,
    row_seconds: 0,
    search_wait_seconds: 0,
    transient: 0,
  },
  created_at: "2026-01-01T00:00:00Z",
  id: "01CCCCCCCCCCCCCCCCCCCCCCCC",
  list_id: "01AAAAAAAAAAAAAAAAAAAAAAAA",
  started_by: "01DDDDDDDDDDDDDDDDDDDDDDDD",
  status: "pending",
  updated_at: "2026-01-01T00:00:00Z",
};

test("postFillRefill sends rows only when scoped", async (t) => {
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(JOB_ENVELOPE), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const scoped = await postFillRefill("01AAAAAAAAAAAAAAAAAAAAAAAA", "answer", { rows: 32 });
  assert.equal(scoped.status, "ok");
  const all = await postFillRefill("01AAAAAAAAAAAAAAAAAAAAAAAA", "answer");
  assert.equal(all.status, "ok");

  assert.equal(calls.length, 2);
  assert.ok(calls[0]!.url.endsWith("/columns/answer/refill"));
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), { rows: 32 });
  assert.equal(calls[1]!.init.body, undefined);
});

test("postFillRefill names the resume key the server declares", async (t) => {
  // The server's ColumnRefillRequest declares `resume_fill`, and DRF
  // DROPS a body key it does not declare rather than refusing it. So a
  // client that writes any other name still gets a 201, and the fill
  // it opens covers the whole column instead of the stopped fill's own
  // unresolved rows: unbounded spend against the user's metered key,
  // reported as success. Only the exact key can be asserted here.
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(JOB_ENVELOPE), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const resumed = await postFillRefill("01AAAAAAAAAAAAAAAAAAAAAAAA", "answer", {
    resumeFill: "01FILLAAAAAAAAAAAAAAAAAAAA",
  });
  assert.equal(resumed.status, "ok");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), { resume_fill: "01FILLAAAAAAAAAAAAAAAAAAAA" });
});

test("the known cause vocabulary is read off the contract, not hand-typed", () => {
  // CELL_STATES recovers the enum's members through zod's documented
  // accessor. If that introspection ever returns nothing, the tolerant
  // sidecar read would treat EVERY cause as unknown and render the
  // whole sheet as retryable dots, silently. Pin both the shape and a
  // couple of members.
  assert.ok(CELL_STATES.length >= 8, `expected the full vocabulary, got ${CELL_STATES.length}`);
  assert.ok(CELL_STATES.includes("pending"));
  assert.ok(CELL_STATES.includes("unverified"));
  // Every settled cause is a member of the vocabulary it partitions.
  for (const settled of SETTLED_CELL_STATES) assert.ok(CELL_STATES.includes(settled), settled);
});

test("an unknown fill status reads as running, the value that promises least", async (t) => {
  // web/AGENTS.md: a read whose enum the SERVER owns is tolerant, and
  // the mapping is PINNED. This path only ever runs against a bundle
  // that predates the contract, so nothing in review exercises it. A
  // strict parse would fail the whole page, which use-fill reads as a
  // blip: the poll backs off to its ceiling and the sheet says updates
  // are not reaching it while the fill runs perfectly well.
  //
  // RUNNING is the least-promising member: it keeps the loop alive and
  // claims nothing terminal.
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ items: [{ ...JOB_ENVELOPE, status: "paused" }], columns: [], next_cursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;

  const res = await getFills("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.items[0]!.status, "running");
});

test("a known fill status is passed through untouched", async (t) => {
  // The tolerance must not flatten every status to running: only a
  // member this bundle has never heard of maps.
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        items: [
          { ...JOB_ENVELOPE, id: "01A", status: "complete" },
          { ...JOB_ENVELOPE, id: "01B", status: "cancelled" },
        ],
        columns: [],
        next_cursor: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;

  const res = await getFills("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.deepEqual(
    res.data.items.map((item) => item.status),
    ["complete", "cancelled"],
  );
});

test("an unknown cell cause maps to a CLIENT member, not a server state", async (t) => {
  // It must not land on model_error: that renders "The model errored",
  // a tier-1 claim about WHY, which a bundle that has never heard of
  // the cause cannot make. It sorts retryable, which is the honest
  // half: an unrecognised cause has not been shown to settle.
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        items: [{ id: "01R", position: 1, data: {}, states: { answer: "a_cause_from_the_future" } }],
        next_cursor: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;

  const res = await fetchListRows("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.items[0]!.states.answer, UNKNOWN_CELL_STATE);
  assert.ok(!(CELL_STATES as readonly string[]).includes(UNKNOWN_CELL_STATE));
});

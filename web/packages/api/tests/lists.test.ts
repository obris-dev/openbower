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
  DEFAULT_WEBHOOK_CADENCE_SECONDS,
  WEBHOOK_CADENCE_SECONDS,
  getColumnWebhook,
  postColumnWebhook,
  postColumnWebhookPreview,
  postColumnWebhookTest,
  postFillRefill,
  reorderColumns,
  updateColumnWebhook,
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

// A syntactically valid run envelope (values are fixtures, not
// meaning): the refill test only cares that the 201 parses.
const RUN_ENVELOPE = {
  agent_id: "01BBBBBBBBBBBBBBBBBBBBBBBB",
  column_keys: ["answer"],
  confirmed_row_count: 10,
  counters: {
    attempted: 0,
    blank: 0,
    filled: 0,
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
    return new Response(JSON.stringify(RUN_ENVELOPE), {
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
    return new Response(JSON.stringify(RUN_ENVELOPE), {
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
    new Response(JSON.stringify({ runs: [{ ...RUN_ENVELOPE, status: "paused" }], columns: [], next_cursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;

  const res = await getFills("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.runs[0]!.status, "running");
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
        runs: [
          { ...RUN_ENVELOPE, id: "01A", status: "complete" },
          { ...RUN_ENVELOPE, id: "01B", status: "cancelled" },
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
    res.data.runs.map((run) => run.status),
    ["complete", "cancelled"],
  );
});

test("an unknown summary status claims nothing; known members and the empty string pass", async (t) => {
  // current_status is the same server-owned enum plus "": a member
  // this bundle predates must not fail the whole poll, and it maps to
  // "", the member that CLAIMS nothing (current_status is rendered:
  // the glance's Filling, the failed word, the recovery verb). A live
  // run of an unheard-of status still rides the runs page, whose own
  // tolerance keeps the loop alive.
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  const columns = [
    { column_key: "a", current_fill_id: "01A", current_status: "a_status_from_the_future", last_error: null, filled: 0, attempted: 0 },
    { column_key: "b", current_fill_id: "", current_status: "", last_error: null, filled: 0, attempted: 0 },
    { column_key: "c", current_fill_id: "01C", current_status: "failed", last_error: { code: "x", message: "y" }, filled: 1, attempted: 2 },
    // A server from before the two fields shipped omits them; absence
    // must read as the never-ran story, never fail the page.
    { column_key: "d", current_fill_id: "", filled: 0, attempted: 0 },
  ];
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ runs: [], columns, next_cursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;

  const res = await getFills("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.deepEqual(
    res.data.columns.map((column) => column.current_status),
    ["", "", "failed", ""],
  );
  assert.equal(res.data.columns[3]!.last_error, null);
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
  // Three shapes on one page: the object the server ships now (with
  // the tool statuses beside the word), an unknown word inside it,
  // and the bare string a server from before tool statuses shipped,
  // which reads as that word with no tool facts.
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        items: [
          { id: "01R", position: 1, data: {}, states: { answer: { state: "a_cause_from_the_future", tools: {} } } },
          {
            id: "01S",
            position: 2,
            data: { answer: "x" },
            states: { answer: { state: "filled", tools: { web_search: "rate_limited" } } },
          },
          { id: "01T", position: 3, data: {}, states: { answer: "no_evidence" }, webhooks: { crm: "waiting" } },
        ],
        next_cursor: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;

  const res = await fetchListRows("01AAAAAAAAAAAAAAAAAAAAAAAA");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  const [future, degraded, bare] = res.data.items;
  assert.deepEqual(future!.states.answer, { state: UNKNOWN_CELL_STATE, tools: {} });
  assert.deepEqual(degraded!.states.answer, { state: "filled", tools: { web_search: "rate_limited" } });
  assert.deepEqual(bare!.states.answer, { state: "no_evidence", tools: {} });
  assert.ok(!(CELL_STATES as readonly string[]).includes(UNKNOWN_CELL_STATE));
  // Webhook cell words ride the row too; a page from before they
  // shipped reads as no words.
  assert.deepEqual(bare!.webhooks, { crm: "waiting" });
  assert.deepEqual(future!.webhooks, {});
});

test("reorderColumns sends the WHOLE key order, and nothing about the columns", async (t) => {
  // The endpoint's guard is that the body is a permutation of what it
  // holds, so the client speaks keys only: a label, a type, or a fill
  // member cannot ride along and be edited by dragging.
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(
      JSON.stringify({
        id: "01AAAAAAAAAAAAAAAAAAAAAAAA",
        label: "Prospects",
        folder_id: "",
        columns: [],
        row_count: 0,
        origin: "manual",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  const res = await reorderColumns("01AAAAAAAAAAAAAAAAAAAAAAAA", ["c", "a", "b"]);
  assert.equal(res.status, "ok");
  assert.ok(calls[0]!.url.endsWith("/column-order"));
  assert.equal(calls[0]!.init.method, "PATCH");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), { keys: ["c", "a", "b"] });
});

// postColumnWebhookTest: the response carries the delivery AND the
// envelope as sent; the envelope is read as plain JSON so a future
// envelope type cannot fail the parse.
test("postColumnWebhookTest parses the delivery and keeps the envelope as JSON", async (t) => {
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  const envelope = { id: "01DLV", type: "future", test: true, data: { type: "future", anything: [1, 2] } };
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        delivery: {
          id: "01DLV",
          destination_id: "01DST",
          type: "digest",
          test: true,
          status: "ok",
          http_status: 200,
          duration_ms: 12,
          error: "",
          response_excerpt: "",
          created_at: "2026-01-01T00:00:00Z",
        },
        envelope,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;

  const res = await postColumnWebhookTest("01AAAAAAAAAAAAAAAAAAAAAAAA", {
    destination_id: "01DST",
    wait_keys: ["answer"],
    payload_keys: ["company"],
    row_id: "01ROW",
    cells: { company: "acme.com" },
  });
  assert.equal(res.status, "ok");
  if (res.status === "ok") {
    assert.equal(res.data.delivery.status, "ok");
    assert.deepEqual(res.data.envelope, envelope);
  }
});

const SUMMARY = {
  id: "01AAAAAAAAAAAAAAAAAAAAAAAA",
  label: "Prospects",
  folder_id: "",
  columns: [{ key: "crm_sync", label: "CRM sync", type: "text", kind: "webhook" as const, node_id: "01NODE" }],
  row_count: 0,
  origin: "manual",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const CONFIG = {
  node_id: "01NODE",
  destination_id: "01DST",
  destination_label: "CRM",
  wait_keys: ["answer"],
  payload_keys: ["company"],
  interval_seconds: 3600,
  enabled: true,
};

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

test("the cadence presets and their default come off the contract", () => {
  assert.ok(WEBHOOK_CADENCE_SECONDS.length > 0);
  assert.ok(WEBHOOK_CADENCE_SECONDS.includes(DEFAULT_WEBHOOK_CADENCE_SECONDS));
});

test("postColumnWebhook posts the config with its label and parses the summary with the webhook member", async (t) => {
  const calls = stubFetch(t, SUMMARY, 201);
  const body = { label: "CRM sync", destination_id: "01DST", wait_keys: ["answer"], payload_keys: ["company"], interval_seconds: 3600 };
  const res = await postColumnWebhook("01AAAAAAAAAAAAAAAAAAAAAAAA", body);
  assert.equal(res.status, "ok");
  if (res.status === "ok") {
    const column = res.data.columns[0]!;
    assert.equal(column.kind, "webhook");
    assert.equal(column.kind === "webhook" ? column.node_id : null, "01NODE");
  }
  assert.ok(calls[0]!.url.endsWith("/columns/webhook"));
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), body);
});

test("getColumnWebhook and updateColumnWebhook address the column by an encoded key and parse the config", async (t) => {
  const calls = stubFetch(t, CONFIG);
  const got = await getColumnWebhook("01AAAAAAAAAAAAAAAAAAAAAAAA", "crm_sync");
  assert.equal(got.status, "ok");
  if (got.status === "ok") assert.deepEqual(got.data, CONFIG);
  assert.ok(calls[0]!.url.endsWith("/columns/webhook/crm_sync"));
  assert.equal(calls[0]!.init.method ?? "GET", "GET");
  const patch = { destination_id: "01DST", wait_keys: ["answer"], payload_keys: ["company"], interval_seconds: 300, enabled: false };
  await updateColumnWebhook("01AAAAAAAAAAAAAAAAAAAAAAAA", "crm_sync", patch);
  assert.equal(calls[1]!.init.method, "PATCH");
  const sent = JSON.parse(String(calls[1]!.init.body));
  assert.deepEqual(sent, patch);
  assert.ok(!("label" in sent));
});

test("postColumnWebhookPreview keeps a future envelope as JSON and sends the column key when given", async (t) => {
  const envelope = { id: "01ENV", type: "future", test: true, data: { type: "future" } };
  const calls = stubFetch(t, { envelope });
  const res = await postColumnWebhookPreview("01AAAAAAAAAAAAAAAAAAAAAAAA", {
    key: "crm_sync",
    destination_id: "01DST",
    wait_keys: ["answer"],
    payload_keys: ["company"],
    row_id: "01ROW",
    cells: { company: "acme.com" },
  });
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data.envelope, envelope);
  assert.ok(calls[0]!.url.endsWith("/columns/webhook/preview"));
  assert.equal(JSON.parse(String(calls[0]!.init.body)).key, "crm_sync");
});

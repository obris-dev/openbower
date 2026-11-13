// Creating a signal: the catalog offers only the signals this bundle has a
// form for, a preview's status and outcomes read tolerantly (held to the
// contract's own enums), and the create posts the setup whole.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { PreviewRowWireSchema, PreviewRunWireSchema } from "@bower/schema";

import {
  PREVIEW_OUTCOME,
  PREVIEW_STATUS,
  SIGNAL,
  UNKNOWN_PREVIEW_OUTCOME,
  UNKNOWN_PREVIEW_STATUS,
  cancelSignalPreview,
  createLog,
  createSignalPreview,
  fetchSignalEvents,
  fetchSignalsCatalog,
  fetchSignalPreview,
  isPreviewOpen,
} from "../src/index.ts";

const LIMITS = { label_max_length: 200, max_listeners: 100, preview_max_events: 20, prompt_max_length: 8000 };
const KIND = { filter: {}, event: {}, min_confidence_default: 0.8 };

const EVENT = {
  id: "01JQ" + "E".repeat(22),
  signal: "funding",
  logged_at: "2026-11-12T12:00:00+00:00",
  data: {
    company: { name: "Acme", domain: "acme.example", hq: null, description: null },
    amount: 5_000_000,
    currency: "USD",
    stage: "seed",
    instruments: ["equity"],
    lead_investors: [],
    other_investors: [],
  },
  source: { title: "Acme raises a seed round", summary: "", url: "https://acme.example/news", published: null },
};

function preview(status: string, outcome: string) {
  return {
    id: "01JQ" + "P".repeat(22),
    signal: "funding",
    filter: { stages: null, min_amount: null, max_amount: null, currency: null },
    prompt: "",
    min_confidence: 0.8,
    user_id: "u1",
    status,
    error: null,
    retry_at: null,
    created_at: "2026-11-12T12:00:00+00:00",
    finished_at: null,
    rows: [{ outcome, confidence: null, reason: "", error: null, event: EVENT }],
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

function silenceErrors(t: { after: (fn: () => void) => void }) {
  const reported: unknown[][] = [];
  const realError = console.error;
  t.after(() => {
    console.error = realError;
  });
  console.error = (...args: unknown[]) => {
    reported.push(args);
  };
  return reported;
}

test("the catalog offers the signals this bundle knows, and reports the rest once", async (t) => {
  // FAILS if an unknown signal is offered: a form for it could not say
  // what it sends.
  const reported = silenceErrors(t);
  stubFetch(t, { signals: [{ signal: "funding", ...KIND }, { signal: "hiring", ...KIND }], limits: LIMITS });
  const res = await fetchSignalsCatalog();
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data.signals.map((kind) => kind.signal), [SIGNAL.FUNDING]);
  assert.equal(reported.length, 1);
  assert.match(String(reported[0]?.[0]), /hiring/);
});

test("a catalog of known signals reports nothing", async (t) => {
  const reported = silenceErrors(t);
  stubFetch(t, { signals: [{ signal: "funding", ...KIND }], limits: LIMITS });
  const res = await fetchSignalsCatalog();
  assert.equal(res.status, "ok");
  assert.equal(reported.length, 0);
});

test("recent events ask for the limit given", async (t) => {
  const calls = stubFetch(t, { items: [EVENT], next_cursor: null });
  const res = await fetchSignalEvents(SIGNAL.FUNDING, 20);
  assert.equal(res.status, "ok");
  assert.ok(calls[0]?.url.endsWith("/v1/signals/funding/events?limit=20"));
});

test("a preview posts the setup and the events chosen", async (t) => {
  const calls = stubFetch(t, preview("pending", "waiting"), 202);
  const res = await createSignalPreview(SIGNAL.FUNDING, { filter: { stages: ["seed"] }, prompt: "B2B" }, [EVENT.id]);
  assert.equal(res.status, "ok");
  assert.ok(calls[0]?.url.endsWith("/v1/signals/previews"));
  assert.deepEqual(JSON.parse(String(calls[0]?.init.body)), {
    signal: "funding",
    filter: { stages: ["seed"] },
    prompt: "B2B",
    event_ids: [EVENT.id],
  });
});

test("known statuses and outcomes pass through untouched", async (t) => {
  for (const status of Object.values(PREVIEW_STATUS)) {
    stubFetch(t, preview(status, "passed"));
    const res = await fetchSignalPreview("p1");
    assert.equal(res.status === "ok" && res.data.status, status);
  }
  for (const outcome of Object.values(PREVIEW_OUTCOME)) {
    stubFetch(t, preview("complete", outcome));
    const res = await fetchSignalPreview("p1");
    assert.equal(res.status === "ok" && res.data.rows[0]?.outcome, outcome);
  }
});

test("an unknown status and outcome read as the client-only members", async (t) => {
  // FAILS if either maps to a real member: an unknown outcome read as
  // matched would claim a match, an unknown status read as open would
  // poll forever.
  stubFetch(t, preview("queued_elsewhere", "maybe"));
  const res = await fetchSignalPreview("p1");
  assert.equal(res.status, "ok");
  if (res.status === "ok") {
    assert.equal(res.data.status, UNKNOWN_PREVIEW_STATUS);
    assert.equal(res.data.rows[0]?.outcome, UNKNOWN_PREVIEW_OUTCOME);
  }
});

test("a failure, an error, a stage or an instrument the server adds never fails the preview", async (t) => {
  // FAILS if any nested value set is read strictly: one added value would
  // turn a finished preview into a page that lost sight of it.
  const later = preview("failed", "errored");
  later.error = { kind: "a_failure_added_later" } as unknown as null;
  const row = later.rows[0]!;
  row.error = { kind: "an_error_added_later" } as unknown as null;
  row.event = {
    ...EVENT,
    data: { ...EVENT.data, stage: "series_z", instruments: ["equity", "an_instrument_added_later"] },
  };
  stubFetch(t, later);
  const res = await fetchSignalPreview("p1");
  assert.equal(res.status, "ok");
  if (res.status !== "ok") return;
  assert.equal(res.data.error?.kind, "unexpected");
  assert.equal(res.data.rows[0]?.error?.kind, "unexpected");
  assert.equal(res.data.rows[0]?.event.data.stage, null);
  assert.deepEqual(res.data.rows[0]?.event.data.instruments, ["equity"]);
});

test("an event page with a stage the server adds still reads", async (t) => {
  stubFetch(t, { items: [{ ...EVENT, data: { ...EVENT.data, stage: "series_z" } }], next_cursor: null });
  const res = await fetchSignalEvents(SIGNAL.FUNDING, 20);
  assert.equal(res.status === "ok" && res.data.items[0]?.data.stage, null);
});

test("a preview is open only while pending or running", async (t) => {
  // FAILS if the unknown status reads as open: an added terminal status
  // would then be polled forever (the poll counts it as a failed read).
  const open = [];
  for (const status of [...Object.values(PREVIEW_STATUS), UNKNOWN_PREVIEW_STATUS]) {
    stubFetch(t, preview(status, "passed"));
    const res = await fetchSignalPreview("p1");
    if (res.status === "ok" && isPreviewOpen(res.data)) open.push(status);
  }
  assert.deepEqual(open, [PREVIEW_STATUS.PENDING, PREVIEW_STATUS.RUNNING]);
});

test("PREVIEW_STATUS and PREVIEW_OUTCOME name exactly the contract's", () => {
  assert.deepEqual(new Set(Object.values(PREVIEW_STATUS)), new Set(PreviewRunWireSchema.shape.status.options));
  assert.deepEqual(new Set(Object.values(PREVIEW_OUTCOME)), new Set(PreviewRowWireSchema.shape.outcome.options));
});

test("cancel sends DELETE to the preview", async (t) => {
  const calls = stubFetch(t, preview("canceled", "waiting"));
  const res = await cancelSignalPreview("p1");
  assert.equal(res.status, "ok");
  assert.equal(calls[0]?.init.method, "DELETE");
  assert.ok(calls[0]?.url.endsWith("/v1/signals/previews/p1"));
});

test("create posts the setup and reads the new sheet's id, whatever the listener says", async (t) => {
  const sheet = {
    id: "01JQ" + "S".repeat(22),
    label: "Seed rounds",
    kind: "log",
    origin: "manual",
    origin_ref: "",
    folder_id: "",
    row_count: 0,
    autofill: true,
    columns: [],
    created_at: "2026-11-12T12:00:00+00:00",
    updated_at: "2026-11-12T12:00:00+00:00",
  };
  // A listener in a state this bundle has never heard of: the create
  // landed, so it must still read as created.
  const listener = {
    id: "01JQ" + "L".repeat(22),
    label: "Seed rounds",
    signal: "funding",
    user_id: "u1",
    status: "paused_for_review",
    error: { kind: "a_kind_added_later" },
    version: {
      id: "v1",
      filter: { stages: ["a_stage_added_later"], min_amount: null, max_amount: null, currency: null },
      prompt: "",
      min_confidence: 0.8,
      user_id: "u1",
      created_at: "2026-11-12T12:00:00+00:00",
    },
    created_at: "2026-11-12T12:00:00+00:00",
    updated_at: "2026-11-12T12:00:00+00:00",
  };
  const detail = { sheet, listener, signal: "funding", status: "active", last_polled_at: null };
  const calls = stubFetch(t, detail, 201);
  const res = await createLog({ label: "Seed rounds", signal: SIGNAL.FUNDING, filter: {} });
  assert.equal(res.status === "ok" && res.data.sheet.id, sheet.id);
  assert.ok(calls[0]?.url.endsWith("/v1/logs"));
  assert.deepEqual(JSON.parse(String(calls[0]?.init.body)), {
    label: "Seed rounds",
    signal: "funding",
    filter: {},
  });
});

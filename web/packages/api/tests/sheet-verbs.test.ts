// A sheet's kind decides its verbs, once: the tolerant read turns a kind
// this bundle has not heard of into the client-only member, which offers
// nothing, and the known kinds are held to the contract's own enum.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { SheetSummarySchema } from "@bower/schema";

import {
  SHEET_KIND,
  UNKNOWN_SHEET_KIND,
  fetchSheet,
  fetchSheetsPage,
  offersAnyVerb,
  sheetVerbs,
  webRoutes,
} from "../src/index.ts";

const SHEET = {
  id: "01JQ" + "S".repeat(22),
  label: "Seed raises",
  kind: "standard",
  origin: "manual",
  origin_ref: "",
  folder_id: "",
  row_count: 3,
  autofill: true,
  columns: [],
  created_at: "2026-11-12T12:00:00+00:00",
  updated_at: "2026-11-12T12:00:00+00:00",
};

// One stub per test, restoring the fetch it found; console.error is
// captured the same way, so an unknown kind's report can be asserted.
function stub(t: { after: (fn: () => void) => void }, body: unknown) {
  const realFetch = globalThis.fetch;
  const realError = console.error;
  const errors: string[] = [];
  t.after(() => {
    globalThis.fetch = realFetch;
    console.error = realError;
  });
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })) as typeof fetch;
  console.error = (message: string) => {
    errors.push(message);
  };
  return errors;
}

test("SHEET_KIND names exactly the contract's kinds", () => {
  const named = Object.values(SHEET_KIND).sort();
  const contract = [...SheetSummarySchema.shape.kind.options].sort();
  assert.deepEqual(named, contract);
});

test("an unknown kind on a sheet's detail reads as the client-only member, and says so", async (t) => {
  const errors = stub(t, { ...SHEET, kind: "digest", entry_action_ids: [] });
  const res = await fetchSheet(SHEET.id);
  assert.equal(res.status === "ok" && res.data.kind, UNKNOWN_SHEET_KIND);
  assert.equal(errors.length, 1);
  assert.match(errors[0]!, /"digest"/);
});

test("an unknown kind in the sheets index reads as the client-only member", async (t) => {
  stub(t, { items: [{ ...SHEET, kind: "digest" }], next_cursor: null });
  const res = await fetchSheetsPage();
  assert.equal(res.status === "ok" && res.data.items[0]?.kind, UNKNOWN_SHEET_KIND);
});

test("a known kind passes through, and no error is reported", async (t) => {
  const errors = stub(t, { ...SHEET, kind: SHEET_KIND.LOG, entry_action_ids: [] });
  const res = await fetchSheet(SHEET.id);
  assert.equal(res.status === "ok" && res.data.kind, SHEET_KIND.LOG);
  assert.deepEqual(errors, []);
});

test("a standard sheet offers every verb, with no settings of its own elsewhere", () => {
  const verbs = sheetVerbs({ id: SHEET.id, kind: SHEET_KIND.STANDARD });
  assert.deepEqual(verbs, {
    rename: true,
    delete: true,
    move: true,
    addRows: true,
    addColumns: true,
    autofill: true,
    exportCsv: true,
    settingsHref: null,
  });
});

test("a signal's sheet moves, gains columns, switches autofill and exports; its signal owns the rest", () => {
  const verbs = sheetVerbs({ id: SHEET.id, kind: SHEET_KIND.LOG });
  assert.deepEqual(verbs, {
    rename: false,
    delete: false,
    move: true,
    addRows: false,
    addColumns: true,
    autofill: true,
    exportCsv: true,
    settingsHref: webRoutes.signal(SHEET.id),
  });
});

test("an unknown kind offers nothing, so it shows no menu", () => {
  const verbs = sheetVerbs({ id: SHEET.id, kind: UNKNOWN_SHEET_KIND });
  assert.equal(offersAnyVerb(verbs), false);
  assert.equal(offersAnyVerb(sheetVerbs({ id: SHEET.id, kind: SHEET_KIND.LOG })), true);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import type { RenderableSheetRow, SheetColumn } from "@bower/api";

import { cellVersion, detailsView, hasDetails, opensDetails } from "./details-view.ts";

const company: SheetColumn = { key: "company", label: "Company", type: "text", kind: "plain", immutable: true };
const founder: SheetColumn = { key: "founder", label: "Founder", type: "text", kind: "ai", immutable: false, node_id: "n", current_fill_id: "" };
const send: SheetColumn = { key: "crm", label: "CRM", type: "text", kind: "webhook", immutable: false, node_id: "n" };
const row = (data: Record<string, string>, states: RenderableSheetRow["states"] = {}): RenderableSheetRow =>
  ({ id: "r", created_at: "", data, states }) as RenderableSheetRow;
const keys = { metaKey: false, ctrlKey: false, altKey: false, shiftKey: false };

test("Space alone opens a cell's details; a chord or another key does not", () => {
  assert.equal(opensDetails({ key: " ", ...keys }), true);
  assert.equal(opensDetails({ key: " ", ...keys, shiftKey: true }), false);
  assert.equal(opensDetails({ key: "a", ...keys }), false);
});

test("a value-holding cell has details, a send cell does not", () => {
  assert.equal(hasDetails(company), true);
  assert.equal(hasDetails(founder), true);
  assert.equal(hasDetails(send), false);
});

test("the row is named by its first plain column, never the cell itself", () => {
  const view = detailsView([company, founder], founder, row({ company: "Acme", founder: "Jane Doe" }));
  assert.deepEqual(view, { rowLabel: "Acme", value: "Jane Doe", unconfirmed: "", agent: true });
  assert.equal(detailsView([company, founder], company, row({ company: "Acme" })).rowLabel, "");
});

test("a blank unverified cell reads its unconfirmed answer, a value outranks it", () => {
  const states = { founder: { state: "unverified" as const, tools: {}, unconfirmed: "Jane Doe" } };
  // FAILS if the panel loses the answer the grid shows.
  assert.equal(detailsView([company, founder], founder, row({ company: "Acme" }, states)).unconfirmed, "Jane Doe");
  assert.equal(detailsView([company, founder], founder, row({ founder: "Ann Lee" }, states)).unconfirmed, "");
});

test("a cell's version moves with what it holds, never with another column", () => {
  const before = row({ company: "Acme" }, { founder: { state: "unverified", tools: {}, unconfirmed: "Jane Doe" } });
  const used = row({ company: "Acme", founder: "Jane Doe" }, {});
  const landed = row({ company: "Acme" }, { founder: { state: "no_evidence", tools: {} } });
  const other = row({ company: "Acme Inc" }, { founder: { state: "unverified", tools: {}, unconfirmed: "Jane Doe" } });
  // FAILS if a write, a new state or another column misreads as a change.
  assert.notEqual(cellVersion(before, "founder"), cellVersion(used, "founder"));
  assert.notEqual(cellVersion(before, "founder"), cellVersion(landed, "founder"));
  assert.equal(cellVersion(before, "founder"), cellVersion(other, "founder"));
  // A filled cell carries no state: an edit changes its value alone.
  assert.notEqual(cellVersion(row({ founder: "Jane" }), "founder"), cellVersion(row({ founder: "Janet" }), "founder"));
  const unsure = (answer: string) => row({}, { founder: { state: "unverified", tools: {}, unconfirmed: answer } });
  assert.notEqual(cellVersion(unsure("Jane"), "founder"), cellVersion(unsure("Janet"), "founder"));
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { bodyFor, initialDraft, isDirty, withSiblings } from "./config.ts";

const COLUMNS = [
  { key: "company", label: "Company", type: "text" as const, kind: "plain" as const },
  { key: "answer", label: "Answer", type: "text" as const, kind: "ai" as const, node_id: "01NODE", current_fill_id: "" },
  { key: "score", label: "Score", type: "text" as const, kind: "ai" as const, node_id: "01NODE", current_fill_id: "" },
  { key: "crm_sync", label: "CRM sync", type: "text" as const, kind: "webhook" as const, node_id: "01HOOK" },
];
const SAVED = {
  node_id: "01HOOK",
  destination_id: "01DST",
  destination_label: "CRM",
  wait_keys: ["score", "answer"],
  payload_keys: ["company", "gone"],
  interval_seconds: 3600,
  enabled: true,
};

test("a fresh draft waits on every AI column and sends every data column at the default cadence", () => {
  const draft = initialDraft(COLUMNS, null, { intervalSeconds: 900 });
  assert.equal(draft.destinationId, "");
  assert.deepEqual([...draft.waitKeys], ["answer", "score"]);
  // The webhook column itself never rides a payload.
  assert.deepEqual([...draft.payloadKeys], ["company", "answer", "score"]);
  assert.equal(draft.intervalSeconds, 900);
  assert.equal(draft.enabled, true);
});

test("an edit draft is the saved config intersected with the sheet's columns", () => {
  const draft = initialDraft(COLUMNS, SAVED, { intervalSeconds: 900 });
  assert.equal(draft.destinationId, "01DST");
  assert.deepEqual([...draft.payloadKeys], ["company"]);
  assert.equal(draft.intervalSeconds, 3600);
});

test("bodyFor lists keys in sheet order whatever order they were chosen in", () => {
  const draft = initialDraft(COLUMNS, SAVED, { intervalSeconds: 900 });
  assert.deepEqual(bodyFor(draft, COLUMNS).wait_keys, ["answer", "score"]);
});

test("isDirty compares the draft to the seeded draft: sets, not orders, and every scalar", () => {
  const saved = { ...SAVED, payload_keys: ["company"] };
  const seeded = initialDraft(COLUMNS, saved, { intervalSeconds: 900 });
  assert.equal(isDirty(seeded, seeded), false);
  assert.equal(isDirty({ ...seeded, intervalSeconds: 300 }, seeded), true);
  assert.equal(isDirty({ ...seeded, enabled: false }, seeded), true);
  assert.equal(isDirty({ ...seeded, waitKeys: new Set(["answer"]) }, seeded), true);
});

test("a stored key the sheet has lost does not make the form dirty on open", () => {
  // The intersection drops the key from the draft; comparing against
  // the raw config would read that drop as the user's change.
  const stale = { ...SAVED, payload_keys: ["company", "gone"] };
  const seeded = initialDraft(COLUMNS, stale, { intervalSeconds: 900 });
  assert.equal(seeded.payloadKeys.has("gone"), false);
  assert.equal(isDirty(seeded, initialDraft(COLUMNS, stale, { intervalSeconds: 900 })), false);
});

test("withSiblings carries an agent's other columns with the one toggled", () => {
  // answer and score share a node; country has its own.
  const all = new Set(["answer", "score", "country"]);
  assert.deepEqual([...withSiblings(COLUMNS, all, new Set(["score", "country"]))].sort(), ["country"]);
  assert.deepEqual([...withSiblings(COLUMNS, new Set(["country"]), new Set(["country", "answer"]))].sort(), [
    "answer",
    "country",
    "score",
  ]);
  assert.deepEqual([...withSiblings(COLUMNS, all, new Set(["answer", "score"]))].sort(), ["answer", "score"]);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { bodyFor, initialDraft, isDirty } from "./config.ts";

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

test("isDirty compares sets, not orders, and every scalar", () => {
  const draft = initialDraft(COLUMNS, { ...SAVED, payload_keys: ["company"] }, { intervalSeconds: 900 });
  assert.equal(isDirty(draft, { ...SAVED, payload_keys: ["company"] }), false);
  assert.equal(isDirty({ ...draft, intervalSeconds: 300 }, { ...SAVED, payload_keys: ["company"] }), true);
  assert.equal(isDirty({ ...draft, enabled: false }, { ...SAVED, payload_keys: ["company"] }), true);
  assert.equal(isDirty({ ...draft, waitKeys: new Set(["answer"]) }, { ...SAVED, payload_keys: ["company"] }), true);
});

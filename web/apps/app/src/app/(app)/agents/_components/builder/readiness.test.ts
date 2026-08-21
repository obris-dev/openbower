import assert from "node:assert/strict";
import { test } from "node:test";

import { buildChecklist, firstGap } from "./readiness.ts";

const missing = { label: true, prompt: false, model: true, outputs: false };

test("save includes the name; test does not", () => {
  assert.deepEqual(
    buildChecklist("save", missing).map((item) => item.key),
    ["label", "prompt", "model", "outputs"],
  );
  assert.deepEqual(
    buildChecklist("test", missing).map((item) => item.key),
    ["prompt", "model", "outputs"],
  );
});

test("the first gap follows the attempted action's order", () => {
  assert.equal(firstGap("save", missing), "agent-label");
  assert.equal(firstGap("test", missing), "agent-model");
  assert.equal(firstGap("test", { ...missing, model: false }), undefined);
});

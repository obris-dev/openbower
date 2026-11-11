import assert from "node:assert/strict";
import { test } from "node:test";

import { cadenceLabel, cadenceOptions } from "./cadence.ts";

test("cadenceLabel picks the largest whole unit", () => {
  assert.equal(cadenceLabel(300), "5 minutes");
  assert.equal(cadenceLabel(900), "15 minutes");
  assert.equal(cadenceLabel(3600), "1 hour");
  assert.equal(cadenceLabel(21_600), "6 hours");
  assert.equal(cadenceLabel(86_400), "1 day");
  assert.equal(cadenceLabel(60), "1 minute");
});

test("cadenceOptions keeps a stored value the presets no longer hold, once, in order", () => {
  assert.deepEqual(cadenceOptions([300, 900, 3600], 900), [300, 900, 3600]);
  assert.deepEqual(cadenceOptions([300, 900, 3600], 1800), [300, 900, 1800, 3600]);
});

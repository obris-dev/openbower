// A signal's sheet: an empty period says the period is empty, never the
// sheet.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { PERIOD, PERIODS } from "../lib/periods.ts";
import { PERIOD_NAMES, emptyPeriodNote } from "./copy.ts";

test("an empty period names the period", () => {
  assert.equal(emptyPeriodNote(PERIOD.DAY), "No rows logged in the last 24 hours.");
  assert.doesNotMatch(emptyPeriodNote(PERIOD.WEEK), /sheet/);
});

test("every period has its name", () => {
  for (const period of PERIODS) assert.ok(PERIOD_NAMES[period].startsWith("Last "));
});

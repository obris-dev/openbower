// A signal's sheet: rolling periods back from now, and a URL that names
// nothing it knows reading as the default.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { DEFAULT_PERIOD, PERIOD, periodFrom, sinceFor } from "./periods.ts";

const NOW = Date.parse("2026-11-13T15:30:00.000Z");

test("each period starts that long before now", () => {
  assert.equal(sinceFor(PERIOD.DAY, NOW), "2026-11-12T15:30:00.000Z");
  assert.equal(sinceFor(PERIOD.WEEK, NOW), "2026-11-06T15:30:00.000Z");
  assert.equal(sinceFor(PERIOD.MONTH, NOW), "2026-10-14T15:30:00.000Z");
  assert.equal(sinceFor(PERIOD.QUARTER, NOW), "2026-08-15T15:30:00.000Z");
  assert.equal(sinceFor(PERIOD.HALF_YEAR, NOW), "2026-05-13T15:30:00.000Z");
});

test("a URL naming no period reads as the default, last 7 days", () => {
  // FAILS if an unknown value reaches the rows: the server would refuse it.
  assert.equal(DEFAULT_PERIOD, PERIOD.WEEK);
  assert.equal(periodFrom("30d"), PERIOD.MONTH);
  assert.equal(periodFrom("all"), DEFAULT_PERIOD);
  assert.equal(periodFrom(undefined), DEFAULT_PERIOD);
  assert.equal(periodFrom(["24h", "7d"]), DEFAULT_PERIOD);
});

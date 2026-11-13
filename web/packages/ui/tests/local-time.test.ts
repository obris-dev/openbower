// A moment reads in the zone it is shown in: the reader's own once the
// browser renders it, UTC (named) in the server render it must match.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { TIME_FORMAT, formatMoment } from "../src/local-time-format.ts";

const NOON_UTC = "2026-11-12T12:01:00+00:00";

test("a minute in a named zone reads in that zone, and names it", () => {
  assert.equal(formatMoment(NOON_UTC, TIME_FORMAT.MINUTE, "UTC"), "Nov 12, 12:01 PM UTC");
  assert.equal(formatMoment(NOON_UTC, TIME_FORMAT.MINUTE, "America/Los_Angeles"), "Nov 12, 4:01 AM PST");
});

test("a day reads in the zone it is shown in, which can be a different day", () => {
  const lateUtc = "2026-11-12T03:00:00+00:00";
  assert.equal(formatMoment(lateUtc, TIME_FORMAT.DAY, "UTC"), "Nov 12");
  assert.equal(formatMoment(lateUtc, TIME_FORMAT.DAY, "America/Los_Angeles"), "Nov 11");
});

test("the reader's own zone is used when none is named, and no zone is printed", () => {
  const local = formatMoment(NOON_UTC, TIME_FORMAT.MINUTE);
  assert.doesNotMatch(local, /UTC|PST|GMT/);
});

test("a value that is not a time reads as nothing", () => {
  assert.equal(formatMoment("not a time", TIME_FORMAT.DAY), "");
});

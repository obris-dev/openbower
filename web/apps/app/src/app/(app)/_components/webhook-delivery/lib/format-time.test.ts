import assert from "node:assert/strict";
import { test } from "node:test";

import { formatTime } from "./format-time.ts";

test("formatTime renders UTC with seconds, whatever the zone the server or browser runs in", () => {
  assert.equal(formatTime("2026-09-16T17:01:12+00:00"), "Sep 16, 17:01:12 UTC");
  assert.equal(formatTime("2026-09-16T19:01:12+02:00"), "Sep 16, 17:01:12 UTC");
});

test("an unparseable time renders nothing rather than an invalid date", () => {
  assert.equal(formatTime("not a date"), "");
});

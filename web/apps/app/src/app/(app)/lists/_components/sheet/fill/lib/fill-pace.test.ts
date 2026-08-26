import assert from "node:assert/strict";
import { test } from "node:test";

import { paceSummary } from "./fill-pace.ts";

const base = { attempted: 0, filled: 0, blank: 0, transient: 0, row_seconds: 0, search_wait_seconds: 0, concurrency_point: 0 };

test("silent until a row landed", () => {
  assert.equal(paceSummary(base), null);
  assert.equal(paceSummary({ ...base, attempted: 3 }), null);
});

test("names the queued-search bottleneck qualitatively", () => {
  assert.equal(
    paceSummary({ ...base, attempted: 10, row_seconds: 100, search_wait_seconds: 160, concurrency_point: 4 }),
    "~10 s per row | mostly waiting on search | 4 wide",
  );
});

test("light search time stays quantitative", () => {
  assert.equal(
    paceSummary({ ...base, attempted: 10, row_seconds: 100, search_wait_seconds: 20, concurrency_point: 2 }),
    "~10 s per row | ~2 s of it search | 2 wide",
  );
});

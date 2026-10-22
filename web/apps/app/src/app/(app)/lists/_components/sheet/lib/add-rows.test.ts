import assert from "node:assert/strict";
import { test } from "node:test";

import { ADD_ROWS_MAX, clampCount } from "./add-rows.ts";

test("a count is pulled inside the bound, and nonsense asks for one row", () => {
  assert.equal(clampCount(10), 10);
  assert.equal(clampCount(0), 1);
  assert.equal(clampCount(-4), 1);
  assert.equal(clampCount(Number.NaN), 1);
  assert.equal(clampCount(2.6), 3);
  assert.equal(clampCount(5000), ADD_ROWS_MAX);
});

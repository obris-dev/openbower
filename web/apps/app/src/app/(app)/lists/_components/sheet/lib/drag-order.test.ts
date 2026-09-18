import assert from "node:assert/strict";
import { test } from "node:test";

import { orderAfterDrag } from "./drag-order.ts";

const columns = ["a", "b", "c", "d"].map((key) => ({
  key,
  label: key.toUpperCase(),
  type: "text" as const,
  fill: null,
  webhook: null,
}));
const landing = (active: string | number, over: string | number | null) => ({
  active: { id: active },
  over: over === null ? null : { id: over },
});

test("a drag onto another column returns the resulting key order", () => {
  assert.deepEqual(orderAfterDrag(columns, landing("a", "c")), ["b", "c", "a", "d"]);
  assert.deepEqual(orderAfterDrag(columns, landing("d", "b")), ["a", "d", "b", "c"]);
});

test("a drop outside every column is not a move", () => {
  assert.equal(orderAfterDrag(columns, landing("a", null)), null);
});

test("a drop back on itself is not a move", () => {
  // Distinct from a no-op order: null is what stops a request for a
  // reorder that did not happen.
  assert.equal(orderAfterDrag(columns, landing("b", "b")), null);
});

test("a key neither side recognises refuses rather than guessing", () => {
  assert.equal(orderAfterDrag(columns, landing("gone", "c")), null);
  assert.equal(orderAfterDrag(columns, landing("a", "gone")), null);
});

test("the result is always a permutation of the input keys", () => {
  const keys = columns.map((column) => column.key);
  for (const from of keys) {
    for (const to of keys) {
      const result = orderAfterDrag(columns, landing(from, to));
      if (result === null) continue;
      assert.deepEqual([...result].sort(), [...keys].sort());
    }
  }
});

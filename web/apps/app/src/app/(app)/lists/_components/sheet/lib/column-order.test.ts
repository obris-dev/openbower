import assert from "node:assert/strict";
import { test } from "node:test";

import { canMove, moveColumn, nudgeColumn } from "./column-order.ts";

const columns = [
  { key: "a", label: "A", type: "text" as const, fill: null },
  { key: "b", label: "B", type: "text" as const, fill: null },
  { key: "c", label: "C", type: "text" as const, fill: null },
];

test("a move returns the resulting key order", () => {
  assert.deepEqual(moveColumn(columns, 0, 2), ["b", "c", "a"]);
  assert.deepEqual(moveColumn(columns, 2, 0), ["c", "a", "b"]);
  assert.deepEqual(moveColumn(columns, 1, 2), ["a", "c", "b"]);
});

test("a move to its own index changes nothing", () => {
  assert.deepEqual(moveColumn(columns, 1, 1), ["a", "b", "c"]);
});

test("an out-of-range move is a NO-OP, never a clamp", () => {
  // A clamp would turn "move left from position 0" into a reorder
  // that looks like it worked and sends a request that did nothing.
  assert.deepEqual(moveColumn(columns, 0, -1), ["a", "b", "c"]);
  assert.deepEqual(moveColumn(columns, 2, 3), ["a", "b", "c"]);
  assert.deepEqual(moveColumn(columns, -1, 1), ["a", "b", "c"]);
});

test("the ends cannot move outward", () => {
  assert.equal(canMove(columns, "a", -1), false);
  assert.equal(canMove(columns, "c", 1), false);
  assert.equal(canMove(columns, "a", 1), true);
  assert.equal(canMove(columns, "c", -1), true);
  assert.equal(canMove(columns, "missing", 1), false);
});

test("a nudge at an end returns null, not the unchanged order", () => {
  // Null so a caller cannot send a no-op request believing it moved
  // something.
  assert.equal(nudgeColumn(columns, "a", -1), null);
  assert.equal(nudgeColumn(columns, "c", 1), null);
  assert.deepEqual(nudgeColumn(columns, "a", 1), ["b", "a", "c"]);
  assert.deepEqual(nudgeColumn(columns, "c", -1), ["a", "c", "b"]);
});

test("every result is a permutation: the endpoint refuses anything else", () => {
  const before = columns.map((c) => c.key).sort();
  for (const [from, to] of [[0, 2], [2, 0], [1, 0], [0, 1]] as const) {
    assert.deepEqual([...moveColumn(columns, from, to)].sort(), before);
  }
});

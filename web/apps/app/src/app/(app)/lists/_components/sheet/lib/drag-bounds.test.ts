import assert from "node:assert/strict";
import { test } from "node:test";

import { clampDragX } from "./drag-bounds.ts";

// A 100px node sitting at 200..300 inside bounds of 100..500.
const node = { left: 200, right: 300 };
const bounds = { left: 100, right: 500 };

test("a move that stays inside the bounds is untouched", () => {
  assert.equal(clampDragX(50, node, bounds), 50);
  assert.equal(clampDragX(-50, node, bounds), -50);
  assert.equal(clampDragX(0, node, bounds), 0);
});

test("travelling left stops with the node's left edge on the bound", () => {
  // -100 puts left at 100, exactly the bound, so it survives.
  assert.equal(clampDragX(-100, node, bounds), -100);
  assert.equal(clampDragX(-101, node, bounds), -100);
  assert.equal(clampDragX(-99999, node, bounds), -100);
});

test("travelling right stops with the node's right edge on the bound", () => {
  assert.equal(clampDragX(200, node, bounds), 200);
  assert.equal(clampDragX(201, node, bounds), 200);
  assert.equal(clampDragX(99999, node, bounds), 200);
});

test("a node wider than its bounds pins to the leading edge", () => {
  // The node cannot satisfy both edges. Pinning to the trailing edge
  // would carry it backwards under a forward-moving pointer.
  const wide = { left: 0, right: 900 };
  assert.equal(clampDragX(9999, wide, bounds), 100);
  assert.equal(clampDragX(-9999, wide, bounds), 100);
});

test("a node already flush with a bound cannot travel further that way", () => {
  const flush = { left: 100, right: 200 };
  assert.equal(clampDragX(-1, flush, bounds), 0);
  assert.equal(clampDragX(300, flush, bounds), 300);
  assert.equal(clampDragX(301, flush, bounds), 300);
});

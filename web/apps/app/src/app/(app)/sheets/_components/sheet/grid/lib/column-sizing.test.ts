import assert from "node:assert/strict";
import { test } from "node:test";

import { clampWidth, DEFAULT_COLUMN_WIDTH, MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH } from "./column-sizing.ts";

test("a width inside the bounds comes back as itself, rounded", () => {
  assert.equal(clampWidth(240), 240);
  assert.equal(clampWidth(240.6), 241);
});

test("a width past either bound pins to that bound", () => {
  assert.equal(clampWidth(1), MIN_COLUMN_WIDTH);
  assert.equal(clampWidth(10_000), MAX_COLUMN_WIDTH);
});

test("not-a-number reads as the default, never as a bound", () => {
  assert.equal(clampWidth(Number.NaN), DEFAULT_COLUMN_WIDTH);
  assert.equal(clampWidth(Number.POSITIVE_INFINITY), DEFAULT_COLUMN_WIDTH);
});

test("the default sits inside the bounds", () => {
  assert.ok(DEFAULT_COLUMN_WIDTH >= MIN_COLUMN_WIDTH && DEFAULT_COLUMN_WIDTH <= MAX_COLUMN_WIDTH);
});

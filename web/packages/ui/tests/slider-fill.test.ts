// A slider's fill and value: clamped into the range, as the thumb is.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { sliderFill } from "../src/slider-fill.ts";

test("a value outside the range draws where the thumb does", () => {
  // FAILS if the raw value drives the fill: a stored value past the range
  // would draw a fill below 0% or past 100% while the thumb sits at an end.
  assert.deepEqual(sliderFill(0.3, 0.5, 1), { value: 0.5, percent: 0 });
  assert.deepEqual(sliderFill(1.2, 0.5, 1), { value: 1, percent: 100 });
  assert.deepEqual(sliderFill(0.75, 0.5, 1), { value: 0.75, percent: 50 });
  assert.deepEqual(sliderFill(3, 2, 2), { value: 2, percent: 0 });
});

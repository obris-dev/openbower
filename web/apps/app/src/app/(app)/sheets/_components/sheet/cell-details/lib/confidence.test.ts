import assert from "node:assert/strict";
import test from "node:test";

import { CONFIDENCE_LEVELS } from "@bower/api";

import { CONFIDENCE_BARS, levelWord, litBars } from "./confidence.ts";

test("each level lights one more segment than the level under it", () => {
  const lit = [...CONFIDENCE_LEVELS].reverse().map(litBars);
  assert.deepEqual(lit, [1, 2, 3, 4, 5]);
  assert.equal(Math.max(...lit), CONFIDENCE_BARS);
});

test("every contract level has a word, and an unknown one says nothing", () => {
  for (const level of CONFIDENCE_LEVELS) assert.match(levelWord(level), /confidence$/);
  assert.equal(levelWord(null), "");
});

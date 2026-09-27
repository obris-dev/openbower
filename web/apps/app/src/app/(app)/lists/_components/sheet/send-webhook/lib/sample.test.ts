import assert from "node:assert/strict";
import { test } from "node:test";

import { cellsFor, sampleFrom } from "./sample.ts";

const ROW = { id: "01ROW", data: { company: "acme.com", answer: "yes" } };

test("sampleFrom takes the row's values and blanks a key the row lacks", () => {
  assert.deepEqual(sampleFrom(ROW, ["company", "score"]), { company: "acme.com", score: "" });
});

test("a sheet with no rows yields blank values", () => {
  assert.deepEqual(sampleFrom(null, ["company"]), { company: "" });
});

test("cellsFor sends exactly the payload keys, edited values winning", () => {
  const values = { company: "edited.example", answer: "yes", score: "9" };
  assert.deepEqual(cellsFor(values, ["company", "answer"]), { company: "edited.example", answer: "yes" });
  // A key with no value yet still rides, blank: the server requires
  // the set to match.
  assert.deepEqual(cellsFor({}, ["company"]), { company: "" });
});

// The Signals tab's copy: a signal type reads as a person would say it,
// and a delete says what goes.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { deleteConsequence, signalType } from "./copy.ts";

test("a signal type is its key, title-cased", () => {
  assert.equal(signalType("funding"), "Funding");
  assert.equal(signalType("job_change"), "Job Change");
});

test("a delete says what goes, counted", () => {
  assert.match(deleteConsequence(1), /its 1 row are deleted/);
  assert.match(deleteConsequence(1204), /its 1,204 rows are deleted/);
  assert.match(deleteConsequence(0), /its sheet is deleted/);
  assert.doesNotMatch(deleteConsequence(0), /0 rows/);
});

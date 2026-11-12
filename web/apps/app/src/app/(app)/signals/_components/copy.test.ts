// The Signals tab's copy: every status the client knows has its pill, and
// a signal type reads as a person would say it.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { LOG_STATUS, UNKNOWN_LOG_STATUS } from "@bower/api";

import { STATUS_PILL, deleteConsequence, signalType } from "./copy.ts";

test("every known status has a pill and the unknown one has none", () => {
  for (const status of Object.values(LOG_STATUS)) assert.ok(STATUS_PILL[status], status);
  assert.equal(STATUS_PILL[UNKNOWN_LOG_STATUS], null);
});

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

// The Signals tab's copy: a signal type reads as a person would say it,
// and a delete says what goes.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { GONE_NOTE, deleteConsequence, noSetupNote, signalType } from "./copy.ts";

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

test("a signal with no setup to edit says why, blaming the app only when it is the app", () => {
  // FAILS if a type the service stopped offering is blamed on this app
  // version, which knows it: a client note claiming knowledge it lacks.
  assert.equal(noSetupNote({ listenerGone: true, signal: "funding", known: true }), GONE_NOTE);
  assert.match(noSetupNote({ listenerGone: false, signal: "funding", known: true }), /service isn't offering Funding/);
  assert.match(noSetupNote({ listenerGone: false, signal: "job_change", known: false }), /version of the app can't edit a Job Change/);
});

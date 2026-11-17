import assert from "node:assert/strict";
import { test } from "node:test";

import { cellMoment } from "./cell-moment.ts";

test("a datetime cell in the stored UTC form renders as a moment", () => {
  assert.equal(cellMoment("datetime", "2026-11-16T14:30:00Z"), "2026-11-16T14:30:00Z");
});

test("any other form, or any other type, renders as text", () => {
  // FAILS if a value outside the stored form renders as a time: a
  // zoneless one would be read in a zone its sender never named, and none
  // of these is a value the server's rule wrote.
  assert.equal(cellMoment("datetime", "2026-11-16T14:30:00"), null);
  assert.equal(cellMoment("datetime", "2026-11-16T09:30-05:00"), null);
  assert.equal(cellMoment("datetime", "2026-11-16"), null);
  assert.equal(cellMoment("datetime", "soon"), null);
  assert.equal(cellMoment("text", "2026-11-16T14:30:00Z"), null);
});

test("a value in the stored shape that is no real moment renders as text", () => {
  // FAILS if the shape alone decides: 25:00 would render a blank cell,
  // and Feb 30 a different moment (Mar 2) than the one stored.
  assert.equal(cellMoment("datetime", "2026-11-16T25:00:00Z"), null);
  assert.equal(cellMoment("datetime", "2026-02-30T10:00:00Z"), null);
  assert.equal(cellMoment("datetime", "2026-13-01T00:00:00Z"), null);
});

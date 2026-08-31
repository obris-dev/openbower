import assert from "node:assert/strict";
import { test } from "node:test";
import type { ColumnFillSummary } from "@bower/api";

import { columnProgress, currentRunFor, trackerCell, type TrackerRun } from "./fill-tracker.ts";

function run(overrides: Partial<TrackerRun & { id: string }> = {}): TrackerRun & { id: string } {
  return {
    id: "01RUN",
    confirmed_row_count: 100,
    counters: { filled: 0, attempted: 0 },
    ...overrides,
  };
}

function summary(overrides: Partial<ColumnFillSummary> = {}): ColumnFillSummary {
  return {
    column_key: "email",
    current_fill_id: "01RUN",
    current_status: "running",
    last_error: null,
    filled: 0,
    attempted: 0,
    ...overrides,
  };
}

test("no summary, no named run, or a run off the live page joins to null", () => {
  assert.equal(currentRunFor(undefined, [run()]), null);
  assert.equal(currentRunFor(summary({ current_fill_id: "" }), [run()]), null);
  assert.equal(currentRunFor(summary({ current_fill_id: "01OTHER" }), [run()]), null);
});

test("the column's run is the envelope behind the server's pointer", () => {
  const runs = [run({ id: "01NEW" }), run({ id: "01OLD" })];
  assert.equal(currentRunFor(summary({ current_fill_id: "01OLD" }), runs), runs[1]);
});

test("a column with nothing left says so, rather than showing a zero", () => {
  assert.equal(columnProgress(summary({ filled: 1499, attempted: 2343 }), 2343), "all 2,343 rows run");
});

test("a live run speaks its filled count and its PROCESSED percent", () => {
  // Percent means processed (attempted over confirmed), not
  // productive: 312 of 2343 rows run is 13%, whatever filled says.
  const cell = trackerCell(run({ confirmed_row_count: 2343, counters: { filled: 164, attempted: 312 } }), "running");
  assert.deepEqual(cell, { kind: "live", text: "164 filled | 13% run", fraction: 312 / 2343 });
});

test("counts localize and the fraction (and its percent) clamp to one", () => {
  const cell = trackerCell(run({ confirmed_row_count: 1500, counters: { filled: 1499, attempted: 1600 } }), "running");
  assert.deepEqual(cell, { kind: "live", text: "1,499 filled | 100% run", fraction: 1 });
});

test("a zero confirmed count divides to nothing, never NaN", () => {
  const cell = trackerCell(run({ confirmed_row_count: 0, counters: { filled: 0, attempted: 0 } }), "pending");
  assert.deepEqual(cell, { kind: "live", text: "0 filled | 0% run", fraction: 0 });
});

test("without a live run the summary's status decides the cell", () => {
  // The page ships live runs only, so a terminal story arrives as the
  // summary's current_status: failed keeps a mark on the column, and
  // every quiet ending is the header line alone (a finished run's
  // counters are not replayed; the historic totals are the answer).
  assert.deepEqual(trackerCell(null, "failed"), { kind: "failed", text: "Run failed" });
  assert.deepEqual(trackerCell(null, "complete"), { kind: "none" });
  assert.deepEqual(trackerCell(null, "cancelled"), { kind: "none" });
  assert.deepEqual(trackerCell(null, ""), { kind: "none" });
});

test("a joined run outranks a stale summary status", () => {
  // The payload's two halves come from separate server-side reads
  // (runs first, then summaries), so a run that flips terminal
  // between them ships live in one leg and failed in the other for
  // one poll. The live walk is the current truth for that window.
  const cell = trackerCell(run({ counters: { filled: 1, attempted: 2 } }), "failed");
  assert.deepEqual(cell, { kind: "live", text: "1 filled | 2% run", fraction: 2 / 100 });
});

test("the header answers is there work left, not how well it went", () => {
  // The v4 case from the author's own sheet: a fill scoped to 32 rows
  // of 2,568. What an operator needs from the header is that 2,536
  // rows are still untouched; how well those 32 went belongs in the
  // run status underneath.
  // Only what is KNOWN: a remainder subtracted from the sheet total
  // is not what the button beneath would target, and claiming it
  // put a number in the tracker that the next click contradicted.
  assert.equal(columnProgress(summary({ filled: 17, attempted: 32 }), 2568), "32 of 2,568 rows run");
});

test("a column nothing has run yet names the work, not a zero", () => {
  assert.equal(columnProgress(summary({ filled: 0, attempted: 0 }), 2568), "2,568 rows to fill");
});

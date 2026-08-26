import assert from "node:assert/strict";
import { test } from "node:test";
import type { ColumnFillSummary } from "@bower/api";

import { columnProgress, currentJobFor, trackerCell, type TrackerJob } from "./fill-tracker.ts";

function job(overrides: Partial<TrackerJob & { id: string }> = {}): TrackerJob & { id: string } {
  return {
    id: "01JOB",
    status: "running",
    confirmed_row_count: 100,
    counters: { filled: 0, attempted: 0 },
    ...overrides,
  };
}

function summary(overrides: Partial<ColumnFillSummary> = {}): ColumnFillSummary {
  return { column_key: "email", current_fill_id: "01JOB", filled: 0, attempted: 0, ...overrides };
}

test("no summary, no named job, or a job off the page is the quiet blank", () => {
  assert.equal(currentJobFor(undefined, [job()]), null);
  assert.equal(currentJobFor(summary({ current_fill_id: "" }), [job()]), null);
  assert.equal(currentJobFor(summary({ current_fill_id: "01OTHER" }), [job()]), null);
  assert.deepEqual(trackerCell(null), { kind: "none" });
});

test("the column's job is the envelope behind the server's pointer", () => {
  const jobs = [job({ id: "01NEW" }), job({ id: "01OLD", status: "failed" })];
  assert.equal(currentJobFor(summary({ current_fill_id: "01OLD" }), jobs), jobs[1]);
});

test("a column with nothing left says so, rather than showing a zero", () => {
  assert.equal(columnProgress(summary({ filled: 1499, attempted: 2343 }), 2343), "all 2,343 rows run");
});

test("a live job speaks its filled count and its PROCESSED percent", () => {
  // Percent means processed (attempted over confirmed), not
  // productive: 312 of 2343 rows run is 13%, whatever filled says.
  const cell = trackerCell(job({ confirmed_row_count: 2343, counters: { filled: 164, attempted: 312 } }));
  assert.deepEqual(cell, { kind: "live", text: "164 filled | 13% run", fraction: 312 / 2343 });
});

test("counts localize and the fraction (and its percent) clamp to one", () => {
  const cell = trackerCell(job({ confirmed_row_count: 1500, counters: { filled: 1499, attempted: 1600 } }));
  assert.deepEqual(cell, { kind: "live", text: "1,499 filled | 100% run", fraction: 1 });
});

test("a zero confirmed count divides to nothing, never NaN", () => {
  const cell = trackerCell(job({ confirmed_row_count: 0, counters: { filled: 0, attempted: 0 } }));
  assert.deepEqual(cell, { kind: "live", text: "0 filled | 0% run", fraction: 0 });
});

test("pending is live; failed tints; complete and cancelled are done", () => {
  const base = { counters: { filled: 10, attempted: 20 } };
  assert.equal(trackerCell(job({ ...base, status: "pending" })).kind, "live");
  assert.equal(trackerCell(job({ ...base, status: "failed" })).kind, "failed");
  assert.equal(trackerCell(job({ ...base, status: "complete" })).kind, "done");
  assert.equal(trackerCell(job({ ...base, status: "cancelled" })).kind, "done");
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

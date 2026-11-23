import assert from "node:assert/strict";
import { test } from "node:test";
import type { ColumnFillSummary } from "@bower/api";

import { currentRunFor, trackerLine, type TrackerRun } from "./fill-tracker.ts";

function run(overrides: Partial<TrackerRun & { id: string }> = {}): TrackerRun & { id: string } {
  return {
    id: "01RUN",
    target_row_count: 100,
    counters: { attempted: 0 },
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
    pending: 0,
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

test("a quiet column says how much of the sheet is filled", () => {
  const line = trackerLine(summary({ current_status: "complete", filled: 164, attempted: 180 }), null, 200);
  assert.deepEqual(line, { text: "164 of 200 filled", inProgress: 0, fraction: 0.82, failed: false });
});

test("an autofill is in progress with no fill run behind it", () => {
  // FAILS if the column only speaks for fills: autofill queues open
  // runs on the column's node and nothing else reports them.
  const line = trackerLine(summary({ current_fill_id: "", current_status: "", filled: 164, pending: 3 }), null, 200);
  assert.equal(line.text, "164 of 200 filled | 3 in progress");
  assert.equal(line.inProgress, 3);
});

test("a live fill counts the rows it has not walked yet", () => {
  // A fill queues its rows in slices, so the open runs can be fewer
  // than the rows it will still reach.
  const line = trackerLine(summary({ filled: 10, pending: 4 }), run({ target_row_count: 32, counters: { attempted: 12 } }), 2568);
  assert.equal(line.text, "10 of 2,568 filled | 20 in progress");
});

test("open runs beyond the fill's walk count too (an autofill beside it)", () => {
  const line = trackerLine(summary({ filled: 10, pending: 30 }), run({ target_row_count: 32, counters: { attempted: 12 } }), 200);
  assert.equal(line.inProgress, 30);
});

test("the bar is filled over rows, clamped, and never NaN", () => {
  assert.equal(trackerLine(summary({ filled: 250 }), null, 200).fraction, 1);
  assert.equal(trackerLine(summary({ filled: 0 }), null, 0).fraction, 0);
  assert.equal(trackerLine(summary({ filled: 0 }), null, 0).text, "0 of 0 filled");
});

test("a failed fill marks the column until something runs again", () => {
  assert.equal(trackerLine(summary({ current_status: "failed" }), null, 10).failed, true);
  // The payload's halves come from separate reads, so a live run can
  // ship beside a stale failed status for one poll: the run wins.
  assert.equal(trackerLine(summary({ current_status: "failed" }), run(), 10).failed, false);
  assert.equal(trackerLine(summary({ current_status: "complete" }), null, 10).failed, false);
});

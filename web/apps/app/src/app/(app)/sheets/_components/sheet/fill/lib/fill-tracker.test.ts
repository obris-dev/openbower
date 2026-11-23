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

test("a quiet column is its share filled, broken down in the card", () => {
  const line = trackerLine(summary({ current_status: "complete", filled: 164, attempted: 180 }), null, 200);
  assert.equal(line.percent, 82);
  assert.equal(line.fraction, 0.82);
  assert.equal(line.inProgress, 0);
  assert.deepEqual(line.lines, ["164 of 200 rows filled (82%)", "16 ran with no answer", "20 left to fill"]);
});

test("an autofill is in progress with no fill run behind it", () => {
  // FAILS if the column only speaks for fills: autofill queues open
  // runs on the column's node and nothing else reports them.
  const line = trackerLine(summary({ current_fill_id: "", current_status: "", filled: 164, attempted: 164, pending: 3 }), null, 200);
  assert.equal(line.inProgress, 3);
  assert.deepEqual(line.lines, ["164 of 200 rows filled (82%)", "3 in progress", "33 left to fill"]);
});

test("a live fill counts the rows it has not walked yet", () => {
  // A fill queues its rows in slices, so the open runs can be fewer
  // than the rows it will still reach.
  const line = trackerLine(summary({ filled: 10, attempted: 12, pending: 4 }), run({ target_row_count: 32, counters: { attempted: 12 } }), 2568);
  assert.equal(line.inProgress, 20);
  assert.equal(line.lines[1], "20 in progress");
  assert.equal(line.lines.at(-1), "2,536 left to fill");
});

test("open runs beyond the fill's walk count too (an autofill beside it)", () => {
  const line = trackerLine(summary({ filled: 10, pending: 30 }), run({ target_row_count: 32, counters: { attempted: 12 } }), 200);
  assert.equal(line.inProgress, 30);
});

test("the bar is filled over rows, clamped, and never NaN", () => {
  assert.equal(trackerLine(summary({ filled: 250 }), null, 200).fraction, 1);
  const empty = trackerLine(summary({ filled: 0 }), null, 0);
  assert.deepEqual([empty.fraction, empty.percent, empty.lines], [0, 0, ["0 of 0 rows filled (0%)"]]);
});

test("a full column says so and nothing else", () => {
  assert.deepEqual(trackerLine(summary({ filled: 14, attempted: 14 }), null, 14).lines, ["14 of 14 rows filled (100%)"]);
});

test("a failed fill marks the column until something runs again", () => {
  assert.equal(trackerLine(summary({ current_status: "failed" }), null, 10).failed, true);
  // The payload's halves come from separate reads, so a live run can
  // ship beside a stale failed status for one poll: the run wins.
  assert.equal(trackerLine(summary({ current_status: "failed" }), run(), 10).failed, false);
  assert.equal(trackerLine(summary({ current_status: "complete" }), null, 10).failed, false);
});

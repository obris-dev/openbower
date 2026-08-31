import assert from "node:assert/strict";
import { test } from "node:test";

import { failedLabel, fillingLabel, glanceCounts, type GlanceSummary } from "./glance-counts.ts";

function summary(current_status: GlanceSummary["current_status"], current_fill_id = ""): GlanceSummary {
  return { column_key: `col-${current_status}-${current_fill_id}`, current_fill_id, current_status };
}

const NO_LIVE = new Set<string>();

test("columns count by their newest run's status; quiet states count nowhere", () => {
  const counts = glanceCounts(
    [summary("pending"), summary("running"), summary("failed"), summary("complete"), summary("cancelled"), summary("")],
    NO_LIVE,
  );
  assert.deepEqual(counts, { filling: 2, failed: 1 });
});

test("a column whose run is on the live page counts as filling, whatever its summary says", () => {
  // The payload's two halves come from separate server-side reads, so
  // a run can be live in one and terminal in the other for one poll.
  // The join guards the direction that would LIE: a column the
  // tracker shows walking must never read failed in the footer (the
  // stopped-run test below covers the deliberate reverse window).
  const counts = glanceCounts([summary("failed", "01LIVE"), summary("complete", "01OLD")], new Set(["01LIVE"]));
  assert.deepEqual(counts, { filling: 1, failed: 0 });
});

test("a stopped run's summary keeps the glance filling until the next poll lands", () => {
  // Stop removes the run from the live list at once; the summary
  // still says running for one round trip. The status leg keeps the
  // count honest about what the server last reported, and for that
  // tick the glance is deliberately WIDER than the tracker cell,
  // which has no envelope left to draw a walk from.
  assert.deepEqual(glanceCounts([summary("running", "01GONE")], NO_LIVE), { filling: 1, failed: 0 });
});

test("an idle sheet glances at nothing", () => {
  assert.deepEqual(glanceCounts([summary(""), summary("complete"), summary("cancelled")], NO_LIVE), {
    filling: 0,
    failed: 0,
  });
  assert.equal(fillingLabel(0), null);
  assert.equal(failedLabel(0), null);
});

test("the copy counts columns, singular and plural", () => {
  assert.equal(fillingLabel(1), "Filling 1 column");
  assert.equal(fillingLabel(3), "Filling 3 columns");
  assert.equal(failedLabel(1), "1 column failed");
  assert.equal(failedLabel(2), "2 columns failed");
});

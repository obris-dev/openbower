import assert from "node:assert/strict";
import { test } from "node:test";
import type { FillRunWire } from "@bower/api";

import { isLiveStatus, livenessRead } from "./live-status.ts";

function run(status: FillRunWire["status"], id = "01RUN"): FillRunWire {
  return {
    id,
    list_id: "01LIST",
    agent_id: "01AGENT",
    status,
    column_keys: ["answer"],
    counters: { attempted: 0, filled: 0, blank: 0, transient: 0 },
    confirmed_row_count: 10,
    targeted_at: "2026-01-01T00:00:00Z",
    started_by: "01USER",
    heartbeat_at: "2026-01-01T00:00:00Z",
    error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const NO_SUMMARIES: { current_status: "" }[] = [];

test("the live partition is exactly the two working statuses", () => {
  assert.ok(isLiveStatus("pending"));
  assert.ok(isLiveStatus("running"));
  for (const status of ["complete", "failed", "cancelled", "", "a_status_from_the_future"]) {
    assert.ok(!isLiveStatus(status), status);
  }
});

test("a terminal run on the page is filtered, not trusted", () => {
  // The page promises live runs only; the client holds the line for
  // every KNOWN terminal member (the runs leg's tolerant read
  // normalizes an unknown to running upstream, so one never reaches
  // this filter; the summaries leg maps an unknown to "", not-live).
  const read = livenessRead([run("running", "01A"), run("failed", "01B"), run("cancelled", "01C")], NO_SUMMARIES);
  assert.deepEqual(
    read.runs.map((r) => r.id),
    ["01A"],
  );
});

test("an empty runs leg with a live summary keeps the loop alive", () => {
  // The payload's halves come from separate server-side reads, so a
  // fill admitted between them ships runs: [] beside a pending
  // summary. Exiting on the runs leg alone would leave the glance
  // promising updates with nothing polling behind them.
  const read = livenessRead([], [{ current_status: "pending" }]);
  assert.deepEqual(read.runs, []);
  assert.equal(read.live, true);
});

test("either leg alone is live; both quiet is idle", () => {
  assert.equal(livenessRead([run("running")], NO_SUMMARIES).live, true);
  assert.equal(livenessRead([], [{ current_status: "running" }]).live, true);
  assert.equal(livenessRead([], [{ current_status: "failed" }, { current_status: "" }]).live, false);
  assert.equal(livenessRead([run("complete")], [{ current_status: "complete" }]).live, false);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { ROW_LEASE_STALE_SECONDS } from "@bower/api";

import { staleWarning } from "./fill-staleness.ts";

const CREATED = "2026-01-01T00:00:00Z";
const createdMs = Date.parse(CREATED);
const past = createdMs + (ROW_LEASE_STALE_SECONDS + 60) * 1_000;
// Every run here has its target set whole unless a test says otherwise.
const WHOLE = { targeted_at: CREATED };

test("fresh, terminal, and un-ticked clocks warn about nothing", () => {
  const run = { ...WHOLE, status: "running" as const, heartbeat_at: CREATED };
  assert.equal(staleWarning(run, createdMs + 1_000), null);
  assert.equal(staleWarning({ ...run, status: "complete" as const }, past), null);
  assert.equal(staleWarning(run, 0), null);
});

test("a pending run past the threshold hasn't started, composing support_followup only when it arrived", () => {
  const run = { ...WHOLE, status: "pending" as const, heartbeat_at: CREATED };
  assert.equal(staleWarning(run, past), "The fill hasn't started.");
  assert.equal(staleWarning(run, past, "ask whoever runs this deployment"), "The fill hasn't started; ask whoever runs this deployment.");
});

test("a gone-quiet heartbeat names the silence in minutes", () => {
  const run = { ...WHOLE, status: "running" as const, heartbeat_at: CREATED };
  assert.equal(staleWarning(run, past), `The worker hasn't reported in ${Math.floor((ROW_LEASE_STALE_SECONDS + 60) / 60)}m.`);
});

test("the threshold boundary is exact: at it is fresh, one second past is not", () => {
  // Every other case here sits a minute clear of the line, so flipping
  // <= to < passes them all. These two are the only ones that hold the
  // comparison itself.
  const run = { ...WHOLE, status: "running" as const, heartbeat_at: CREATED };
  const atThreshold = createdMs + ROW_LEASE_STALE_SECONDS * 1_000;
  assert.equal(staleWarning(run, atThreshold), null);
  assert.equal(staleWarning(run, atThreshold + 1_000), `The worker hasn't reported in ${Math.floor((ROW_LEASE_STALE_SECONDS + 1) / 60)}m.`);
});

test("every terminal status warns about nothing, however long the silence", () => {
  // Staleness is degraded REPORTING on a live fill; a fill that ended
  // has nothing left to report, and cancelled and failed were the two
  // the suite never covered.
  for (const status of ["complete", "failed", "cancelled"] as const) {
    const run = { ...WHOLE, status, heartbeat_at: CREATED };
    assert.equal(staleWarning(run, past), null, status);
  }
});

test("a run whose rows are still being queued warns of nothing, however long", () => {
  // Its target set is not whole (targeted_at null): no worker has been
  // offered a row yet, so "hasn't started" would blame the workers for
  // the walk. The warning returns once the walk ends.
  const run = { status: "pending" as const, heartbeat_at: CREATED, targeted_at: null };
  assert.equal(staleWarning(run, past), null);
  assert.equal(staleWarning({ ...run, targeted_at: CREATED }, past), "The fill hasn't started.");
});

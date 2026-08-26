import assert from "node:assert/strict";
import { test } from "node:test";

import { ROW_LEASE_STALE_SECONDS } from "@bower/api";

import { staleWarning } from "./fill-staleness.ts";

const CREATED = "2026-01-01T00:00:00Z";
const createdMs = Date.parse(CREATED);
const past = createdMs + (ROW_LEASE_STALE_SECONDS + 60) * 1_000;

test("fresh, terminal, and un-ticked clocks warn about nothing", () => {
  const job = { status: "running" as const, heartbeat_at: null, created_at: CREATED };
  assert.equal(staleWarning(job, createdMs + 1_000), null);
  assert.equal(staleWarning({ ...job, status: "complete" as const }, past), null);
  assert.equal(staleWarning(job, 0), null);
});

test("a pending job past the threshold hasn't started, composing support_followup only when it arrived", () => {
  const job = { status: "pending" as const, heartbeat_at: null, created_at: CREATED };
  assert.equal(staleWarning(job, past), "The fill hasn't started.");
  assert.equal(staleWarning(job, past, "ask whoever runs this deployment"), "The fill hasn't started; ask whoever runs this deployment.");
});

test("a RUNNING job with no heartbeat is claimed, not unstarted: the copy says what the client knows", () => {
  // The heartbeat stamps only when a row completes, and a first batch
  // can legitimately sit in a long search park, so a running job can
  // pass the whole threshold heartbeat-less; "hasn't started" would be
  // a false claim there.
  const job = { status: "running" as const, heartbeat_at: null, created_at: CREATED };
  assert.equal(staleWarning(job, past), "The fill hasn't reported yet.");
});

test("a gone-quiet heartbeat names the silence in minutes", () => {
  const job = { status: "running" as const, heartbeat_at: CREATED, created_at: "2025-12-31T00:00:00Z" };
  assert.equal(staleWarning(job, past), `The worker hasn't reported in ${Math.floor((ROW_LEASE_STALE_SECONDS + 60) / 60)}m.`);
});

test("the threshold boundary is exact: at it is fresh, one second past is not", () => {
  // Every other case here sits a minute clear of the line, so flipping
  // <= to < passes them all. These two are the only ones that hold the
  // comparison itself.
  const job = { status: "running" as const, heartbeat_at: null, created_at: CREATED };
  const atThreshold = createdMs + ROW_LEASE_STALE_SECONDS * 1_000;
  assert.equal(staleWarning(job, atThreshold), null);
  assert.equal(staleWarning(job, atThreshold + 1_000), "The fill hasn't reported yet.");
});

test("every terminal status warns about nothing, however long the silence", () => {
  // Staleness is degraded REPORTING on a live fill; a fill that ended
  // has nothing left to report, and cancelled and failed were the two
  // the suite never covered.
  for (const status of ["complete", "failed", "cancelled"] as const) {
    const job = { status, heartbeat_at: null, created_at: CREATED };
    assert.equal(staleWarning(job, past), null, status);
  }
});

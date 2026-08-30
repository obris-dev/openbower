import assert from "node:assert/strict";
import { test } from "node:test";

import { anyFailed, anyLive, badgeLabel, soonestEta, trayMode, type TrayRun } from "./fills-tray-mode.ts";

function run(status: TrayRun["status"], attempted: number, confirmed: number): TrayRun {
  return { status, confirmed_row_count: confirmed, counters: { attempted } };
}

test("mode: none, one, many", () => {
  assert.equal(trayMode(0), "empty");
  assert.equal(trayMode(1), "single");
  assert.equal(trayMode(2), "multi");
  assert.equal(trayMode(5), "multi");
});

test("liveness and failure read across every listed run", () => {
  assert.equal(anyLive([run("complete", 8, 8), run("running", 3, 9)]), true);
  assert.equal(anyLive([run("complete", 8, 8), run("cancelled", 3, 9)]), false);
  assert.equal(anyLive([run("pending", 0, 9)]), true);
  assert.equal(anyFailed([run("complete", 8, 8), run("failed", 3, 9)]), true);
  assert.equal(anyFailed([run("running", 3, 9)]), false);
});

test("soonest eta skips unknowns and picks the minimum", () => {
  assert.equal(soonestEta([]), null);
  assert.equal(soonestEta([null, null]), null);
  assert.equal(soonestEta([null, 300, 120]), 120);
  assert.equal(soonestEta([2_460]), 2_460);
});

test("one fill speaks compact counters, several collapse to a count", () => {
  assert.equal(badgeLabel([run("running", 312, 2_568)], null), "312 of 2,568");
  assert.equal(badgeLabel([run("running", 312, 2_568)], 2_460), "312 of 2,568 | ~41 min");
  assert.equal(badgeLabel([run("running", 1, 4), run("pending", 0, 4)], null), "2 fills");
  assert.equal(badgeLabel([run("running", 1, 4), run("pending", 0, 4)], 2_460), "2 fills | ~41 min");
});

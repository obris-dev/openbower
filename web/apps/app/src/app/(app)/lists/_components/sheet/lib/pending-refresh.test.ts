import assert from "node:assert/strict";
import { test } from "node:test";
import type { RenderableListRow } from "@bower/api";

import { PENDING_REFRESH_CEILING_MS, PENDING_REFRESH_FIRST_MS, pendingRefreshDelayMs, pendingSignature } from "./pending-refresh.ts";

function row(id: string, states: RenderableListRow["states"]): Pick<RenderableListRow, "id" | "states"> {
  return { id, states };
}

test("a sheet with no pending cell has nothing to re-read for", () => {
  const rows = [row("01A", { answer: { state: "filled", tools: {} } }), row("01B", {})];
  assert.equal(pendingSignature(rows), "");
});

test("the signature names each pending cell, so a new one restarts the schedule", () => {
  const before = [row("01A", { answer: { state: "pending", tools: {} } }), row("01B", {})];
  const after = [row("01A", { answer: { state: "pending", tools: {} } }), row("01B", { crm: { state: "pending", tools: {} } })];
  assert.equal(pendingSignature(before), "01A:answer");
  assert.equal(pendingSignature(after), "01A:answer 01B:crm");
  assert.notEqual(pendingSignature(before), pendingSignature(after));
});

test("a cell that lands drops out of the signature", () => {
  const landed = [row("01A", { answer: { state: "filled", tools: {} } }), row("01B", { crm: { state: "pending", tools: {} } })];
  assert.equal(pendingSignature(landed), "01B:crm");
});

test("the re-reads back off from one fill poll to the ceiling and stay there", () => {
  assert.equal(pendingRefreshDelayMs(0), PENDING_REFRESH_FIRST_MS);
  assert.equal(pendingRefreshDelayMs(1), PENDING_REFRESH_FIRST_MS * 2);
  assert.equal(pendingRefreshDelayMs(4), PENDING_REFRESH_CEILING_MS);
  assert.equal(pendingRefreshDelayMs(40), PENDING_REFRESH_CEILING_MS);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import type { SheetColumn } from "@bower/api";

import { isEditable, neighbour } from "./cell-navigation.ts";

const columns: SheetColumn[] = [
  { key: "name", label: "Name", type: "text", kind: "plain", immutable: false },
  { key: "w", label: "W", type: "text", kind: "webhook", immutable: false, node_id: "n" },
  { key: "a", label: "A", type: "text", kind: "ai", immutable: false, node_id: "n", current_fill_id: "" },
];
const rows = ["r1", "r2", "r3"];

test("the arrows step over every column, and stop at the edges", () => {
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "name" }, "right"), { rowId: "r1", columnKey: "w" });
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "w" }, "right"), { rowId: "r1", columnKey: "a" });
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "w" }, "down"), { rowId: "r2", columnKey: "w" });
  assert.equal(neighbour(columns, rows, { rowId: "r1", columnKey: "name" }, "left"), null);
  assert.equal(neighbour(columns, rows, { rowId: "r1", columnKey: "a" }, "right"), null);
  assert.equal(isEditable(columns[1]!), false);
});

test("a Tab steps over the editable columns only", () => {
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "name" }, "right", "editable"), {
    rowId: "r1",
    columnKey: "a",
  });
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "a" }, "left", "editable"), {
    rowId: "r1",
    columnKey: "name",
  });
  assert.equal(neighbour(columns, rows, { rowId: "r1", columnKey: "w" }, "right", "editable"), null);
});

test("up and down keep the column and stop at the edges", () => {
  assert.deepEqual(neighbour(columns, rows, { rowId: "r1", columnKey: "name" }, "down"), { rowId: "r2", columnKey: "name" });
  assert.deepEqual(neighbour(columns, rows, { rowId: "r2", columnKey: "name" }, "down"), { rowId: "r3", columnKey: "name" });
  assert.equal(neighbour(columns, rows, { rowId: "r3", columnKey: "name" }, "down"), null);
  assert.equal(neighbour(columns, rows, { rowId: "r1", columnKey: "name" }, "up"), null);
  assert.equal(neighbour(columns, rows, { rowId: "gone", columnKey: "name" }, "up"), null);
});

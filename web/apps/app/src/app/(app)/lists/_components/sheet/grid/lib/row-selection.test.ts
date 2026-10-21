import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NONE,
  count,
  everything,
  headerState,
  isEmpty,
  isSelected,
  pruned,
  toBody,
  toggled,
  withRows,
} from "./row-selection.ts";

test("some rows: toggling adds and removes, a range only adds", () => {
  let selection = toggled(NONE, "a");
  selection = toggled(selection, "b");
  assert.equal(isSelected(selection, "a"), true);
  assert.equal(count(selection, 10), 2);
  selection = toggled(selection, "a");
  assert.equal(isSelected(selection, "a"), false);
  selection = withRows(selection, ["b", "c", "d"]);
  assert.equal(count(selection, 10), 3);
  assert.deepEqual(toBody(selection), { row_ids: ["b", "c", "d"] });
  assert.equal(headerState(selection, 10), "some");
  assert.equal(headerState(selection, 3), "all");
});

test("all rows: unticking excepts, ticking back and a range un-except", () => {
  let selection = everything();
  assert.equal(isSelected(selection, "zzz"), true);
  assert.equal(count(selection, 291), 291);
  assert.equal(headerState(selection, 291), "all");
  selection = toggled(selection, "a");
  assert.equal(isSelected(selection, "a"), false);
  assert.equal(count(selection, 291), 290);
  assert.equal(headerState(selection, 291), "some");
  assert.deepEqual(toBody(selection), { all: true, keep: ["a"] });
  selection = toggled(selection, "b");
  selection = withRows(selection, ["a", "b"]);
  assert.deepEqual(toBody(selection), { all: true, keep: [] });
});

test("every row excepted reads as empty, and the count never goes negative", () => {
  assert.equal(isEmpty(NONE, 5), true);
  let all = everything();
  for (const id of ["a", "b"]) all = toggled(all, id);
  assert.equal(isEmpty(all, 2), true);
  assert.equal(count(all, 1), 0);
  assert.equal(headerState(all, 2), "none");
});

test("pruning drops ids the page no longer holds, and keeps the object when nothing changed", () => {
  const some = withRows(NONE, ["a", "b", "c"]);
  const live = new Set(["a", "c"]);
  assert.deepEqual(toBody(pruned(some, live)), { row_ids: ["a", "c"] });
  assert.equal(pruned(some, new Set(["a", "b", "c"])), some);
  let all = everything();
  all = toggled(all, "gone");
  all = toggled(all, "a");
  assert.deepEqual(toBody(pruned(all, live)), { all: true, keep: ["a"] });
});

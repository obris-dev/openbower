import assert from "node:assert/strict";
import { test } from "node:test";

import { editable, fromJson, previewLines, withEditableCells } from "./preview.ts";

const ENVELOPE = {
  id: "01ENV",
  type: "digest",
  test: true,
  data: {
    type: "digest",
    sheet: { id: "01LIST", label: "Prospects" },
    waited_on: ["answer"],
    items: [{ event_id: "ab12", row_id: "01ROW", position: 1, completed_at: null, cells: { company: "x" }, states: {} }],
  },
};

function render(lines: ReturnType<typeof previewLines>): string {
  return lines
    .filter((line) => !line.add)
    .map((line) => (line.editable ? `${line.text}${JSON.stringify(line.editable.value)}${line.editable.suffix}` : line.text))
    .join("\n");
}

test("withEditableCells replaces the first item's cells with the client's keys in order and leaves the rest as sent", () => {
  const tree = withEditableCells(ENVELOPE, { keys: ["company", "answer"], values: { company: "acme.com" }, canAddColumns: false });
  const item = (tree as { data: { items: Record<string, unknown>[] } }).data.items[0]!;
  assert.deepEqual(item.cells, { company: editable("company", "acme.com"), answer: editable("answer", "") });
  assert.equal(item.event_id, "ab12");
  assert.equal(item.completed_at, null);
  assert.deepEqual(item.states, {});
  // Never a mutation of what the server sent.
  assert.deepEqual(ENVELOPE.data.items[0]!.cells, { company: "x" });
});

test("the add slot renders after the last cell only while a column is missing, and the JSON stays valid", () => {
  const withSlot = previewLines(withEditableCells(ENVELOPE, { keys: ["company"], values: {}, canAddColumns: true }));
  const slot = withSlot.findIndex((line) => line.add);
  assert.ok(slot > 0);
  assert.equal(withSlot[slot - 1]!.editable?.suffix, "");
  assert.equal(withSlot[slot + 1]!.text, "},");
  const without = previewLines(withEditableCells(ENVELOPE, { keys: ["company"], values: {}, canAddColumns: false }));
  assert.ok(without.every((line) => !line.add));
  const parsed = JSON.parse(render(withSlot)) as typeof ENVELOPE;
  assert.equal(parsed.data.items[0]!.cells.company, "");
});

test("an envelope of another shape passes through with no inputs", () => {
  const future = { id: "01ENV", type: "future", data: { type: "future", anything: [1, 2] } };
  const lines = previewLines(withEditableCells(future, { keys: ["company"], values: {}, canAddColumns: true }));
  assert.ok(lines.every((line) => !line.editable && !line.add));
  assert.deepEqual(JSON.parse(render(lines)), future);
});

test("column keys that spell a marker's name are ordinary cells, never markers", () => {
  const tree = withEditableCells(ENVELOPE, { keys: ["editable", "add"], values: { editable: "x" }, canAddColumns: false });
  const lines = previewLines(tree);
  assert.deepEqual(
    lines.filter((line) => line.editable).map((line) => line.editable!.key),
    ["editable", "add"],
  );
  const sent = previewLines(fromJson({ cells: { editable: "e", add: "a" } }));
  assert.ok(sent.every((line) => !line.editable && !line.add));
  assert.ok(sent.some((line) => line.text === '"editable": "e",'));
});

test("previewLines renders JSON one value per line", () => {
  const lines = previewLines({ n: 1, cells: { a: "x" }, empty: {}, list: [] });
  assert.deepEqual(
    lines.map((line) => [line.depth, line.text]),
    [
      [0, "{"],
      [1, '"n": 1,'],
      [1, '"cells": {'],
      [2, '"a": "x"'],
      [1, "},"],
      [1, '"empty": {},'],
      [1, '"list": []'],
      [0, "}"],
    ],
  );
});

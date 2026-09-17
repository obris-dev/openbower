import assert from "node:assert/strict";
import { test } from "node:test";

import { SET_WHEN_SENT, editable, fromJson, previewEnvelope, previewLines } from "./preview.ts";

const INPUT = {
  sheet: { id: "01LIST", label: "Prospects" },
  waitKeys: ["answer"],
  payloadKeys: ["company", "answer"],
  row: { id: "01ROW", position: 1 },
  values: { company: "acme.com", answer: "yes", score: "9" },
  canAddColumns: false,
};

test("previewEnvelope mirrors the contract's digest with markers where the server decides", () => {
  const envelope = previewEnvelope(INPUT) as Record<string, unknown>;
  assert.equal(envelope.type, "digest");
  assert.equal(envelope.test, true);
  assert.equal(envelope.id, SET_WHEN_SENT);
  const data = envelope.data as Record<string, unknown>;
  assert.deepEqual(data.waited_on, ["answer"]);
  const [item] = data.items as Record<string, unknown>[];
  // Only the payload keys ride, each as an editable value; a value the
  // row lacks is blank.
  assert.deepEqual(item!.cells, { company: editable("company", "acme.com"), answer: editable("answer", "yes") });
  assert.equal(item!.states, SET_WHEN_SENT);
  assert.equal(item!.event_id, SET_WHEN_SENT);
  assert.equal(item!.completed_at, SET_WHEN_SENT);
  assert.equal(item!.row_id, "01ROW");
});

test("previewLines renders JSON one value per line, markers flagged", () => {
  const lines = previewLines({ id: SET_WHEN_SENT, n: 1, cells: { a: "x" }, empty: {}, list: [] });
  assert.deepEqual(
    lines.map((line) => [line.depth, line.text, line.placeholder]),
    [
      [0, "{", false],
      [1, '"id": set when sent,', true],
      [1, '"n": 1,', false],
      [1, '"cells": {', false],
      [2, '"a": "x"', false],
      [1, "},", false],
      [1, '"empty": {},', false],
      [1, '"list": []', false],
      [0, "}", false],
    ],
  );
});

test("previewLines splits an editable value around its input", () => {
  const [open, line, close] = previewLines({ cells: { a: editable("a", "x") } }).slice(1, 4);
  assert.deepEqual(line, { depth: 2, text: '"a": ', placeholder: false, editable: { key: "a", value: "x", suffix: "" } });
  assert.equal(open!.text, '"cells": {');
  assert.equal(close!.text, "}");
});

test("previewLines of the preview is valid JSON once markers are quoted and inputs filled", () => {
  const text = previewLines(previewEnvelope(INPUT))
    .map((line) => {
      if (line.placeholder) return line.text.replace("set when sent", '"set when sent"');
      if (line.editable) return `${line.text}${JSON.stringify(line.editable.value)}${line.editable.suffix}`;
      return line.text;
    })
    .join("\n");
  const parsed = JSON.parse(text) as { data: { items: { cells: Record<string, string> }[] } };
  assert.equal(parsed.data.items[0]!.cells.company, "acme.com");
});

test("an add slot renders as a control line after the last cell, only while a column is missing", () => {
  const withSlot = previewLines(previewEnvelope({ ...INPUT, canAddColumns: true }));
  const slot = withSlot.findIndex((line) => line.add);
  assert.ok(slot > 0);
  // The cell before it carries NO comma: the slot is a control, not JSON.
  assert.equal(withSlot[slot - 1]!.editable?.suffix, "");
  assert.equal(withSlot[slot + 1]!.text, "},");
  assert.ok(previewLines(previewEnvelope(INPUT)).every((line) => !line.add));
});

test("fromJson keeps a parsed envelope's shape and renders with no markers", () => {
  const sent = fromJson({ id: "01DLV", test: true, data: { items: [{ event_id: "ab12", cells: { a: "x" } }] } });
  const lines = previewLines(sent);
  assert.ok(lines.every((line) => !line.placeholder));
  assert.ok(lines.some((line) => line.text === '"event_id": "ab12",'));
});

test("column keys that spell a marker's name are ordinary cells, never markers", () => {
  // Keys are user-derived, so `placeholder`, `editable`, and `add` are
  // all legal column keys; a marker must be recognised by its brand.
  const lines = previewLines(
    previewEnvelope({ ...INPUT, payloadKeys: ["placeholder", "editable", "add"], values: { editable: "x" } }),
  );
  const cellLines = lines.filter((line) => line.editable);
  assert.deepEqual(
    cellLines.map((line) => line.editable!.key),
    ["placeholder", "editable", "add"],
  );
  assert.ok(lines.every((line) => !line.add));
  // The same keys coming back from the server render as plain values.
  const sent = previewLines(fromJson({ cells: { placeholder: "p", editable: "e", add: "a" } }));
  assert.ok(sent.every((line) => !line.placeholder && !line.editable && !line.add));
  assert.ok(sent.some((line) => line.text === '"editable": "e",'));
});

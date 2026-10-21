import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_COLUMN_WIDTH, MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH } from "./column-sizing.ts";
import { parseStoredWidths, serializeWidths, storageKey } from "./column-widths.ts";

const keys = ["name", "domain", "revenue"];

test("nothing stored, or garbage, reads as no widths", () => {
  assert.deepEqual(parseStoredWidths(null, keys), {});
  assert.deepEqual(parseStoredWidths("not json", keys), {});
  assert.deepEqual(parseStoredWidths("[1,2]", keys), {});
  assert.deepEqual(parseStoredWidths("42", keys), {});
  assert.deepEqual(parseStoredWidths('{"name":"wide","domain":null}', keys), {});
});

test("a key the sheet no longer has is dropped", () => {
  assert.deepEqual(parseStoredWidths('{"name":300,"gone":300}', keys), { name: 300 });
});

test("a stored width is clamped into bounds and a default is not kept", () => {
  assert.deepEqual(parseStoredWidths('{"name":5,"domain":99999,"revenue":200}', keys), {
    name: MIN_COLUMN_WIDTH,
    domain: MAX_COLUMN_WIDTH,
  });
});

test("widths round-trip, minus the ones at the default", () => {
  const stored = serializeWidths({ name: 320, domain: DEFAULT_COLUMN_WIDTH, revenue: 120.4 });
  assert.deepEqual(parseStoredWidths(stored, keys), { name: 320, revenue: 120 });
});

test("the storage key is per sheet", () => {
  assert.notEqual(storageKey("01A"), storageKey("01B"));
});

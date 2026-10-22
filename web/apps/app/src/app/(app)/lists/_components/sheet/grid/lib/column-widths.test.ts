import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_COLUMN_WIDTH, MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH } from "./column-sizing.ts";
import { webRoutes } from "@bower/api";
import { storedWidthsScript } from "@bower/ui/pre-paint/script";

import {
  parseStoredWidths,
  pendingWidth,
  serializeWidths,
  SHEET_PATH,
  STORAGE_KEY_PREFIX,
  storageKey,
  WIDTH_PROPERTY_PREFIX,
  widthVariable,
} from "./column-widths.ts";

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

test("a pending width reads the sheet-scoped property with the model's width as fallback", () => {
  assert.equal(pendingWidth("01LIST", "name", 200), "var(--sheet-width-01LIST-name, 200px)");
  assert.notEqual(pendingWidth("01LIST", "name", 200), pendingWidth("01OTHER", "name", 200));
});

test("the sheet path the script matches is the one the app routes to, and the id is its capture", () => {
  const id = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const pattern = new RegExp(SHEET_PATH);
  const sheet = webRoutes.list(id);
  const match = sheet.match(pattern);
  assert.equal(match?.[1], id);
  assert.equal("/agents".match(pattern), null);
  const bogus = webRoutes.list("not-an-id");
  assert.equal(bogus.match(pattern), null);
});

/** The pre-paint script run as the browser would on this sheet's
 * path, against a storage holding `raw` under the sheet's key: the
 * properties it stamps, by name. */
function stamped(id: string, raw: string): Record<string, string> {
  const source = storedWidthsScript({
    path: SHEET_PATH,
    storageKeyPrefix: STORAGE_KEY_PREFIX,
    propertyPrefix: WIDTH_PROPERTY_PREFIX,
    min: MIN_COLUMN_WIDTH,
    max: MAX_COLUMN_WIDTH,
  });
  const set: Record<string, string> = {};
  const localStorage = { getItem: (key: string) => (key === storageKey(id) ? raw : null) };
  const document = { documentElement: { style: { setProperty: (name: string, value: string) => void (set[name] = value) } } };
  const location = { pathname: webRoutes.list(id) };
  const script = new Function("localStorage", "document", "location", source);
  script(localStorage, document, location);
  return set;
}

test("the script paints every column at the width the sync will land, under the name a pending cell reads", () => {
  const id = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const raws = [
    '{"name":320,"domain":5,"revenue":99999}',
    '{"name":200.4,"domain":79.5,"revenue":800.5}',
    '{"name":250.6,"domain":250.4,"revenue":-0.4}',
    '{"name":200,"gone":300,"domain":"wide"}',
    "not json",
    "[320]",
  ];
  for (const raw of raws) {
    const parsed = parseStoredWidths(raw, keys);
    const set = stamped(id, raw);
    for (const key of keys) {
      const synced = parsed[key] ?? DEFAULT_COLUMN_WIDTH;
      const name = widthVariable(id, key);
      const painted = set[name] ?? `${DEFAULT_COLUMN_WIDTH}px`;
      assert.equal(painted, `${synced}px`, `${raw} / ${key}`);
      const pending = pendingWidth(id, key, DEFAULT_COLUMN_WIDTH);
      assert.ok(pending.startsWith(`var(${name},`));
    }
  }
});

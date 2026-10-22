import assert from "node:assert/strict";
import { test } from "node:test";

import { storedWidthsScript, type StoredWidthsOptions } from "../src/pre-paint/stored-widths.ts";

const options: StoredWidthsOptions = {
  path: "^/sheets/([0-9A-Z]{26})$",
  storageKeyPrefix: "widths:",
  propertyPrefix: "--w-",
  min: 80,
  max: 800,
};
const ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";

/** Runs the script source against a stand-in page: a location, a
 * storage holding one entry under the id's key, and a root element
 * recording the properties set on it. */
function run(stored: string | null, { pathname = `/sheets/${ID}`, opts = options, throwing = false } = {}) {
  const set: Record<string, string> = {};
  const localStorage = {
    getItem: (key: string) => {
      if (throwing) throw new Error("storage disabled");
      return key === `${opts.storageKeyPrefix}${ID}` ? stored : null;
    },
  };
  const document = { documentElement: { style: { setProperty: (name: string, value: string) => void (set[name] = value) } } };
  const source = storedWidthsScript(opts);
  const script = new Function("localStorage", "document", "location", source);
  script(localStorage, document, { pathname });
  return set;
}

test("stored widths land as pixel properties under the prefix and id, clamped into the bounds", () => {
  assert.deepEqual(run('{"name":320,"domain":5,"revenue":99999,"notes":200.4}'), {
    [`--w-${ID}-name`]: "320px",
    [`--w-${ID}-domain`]: "80px",
    [`--w-${ID}-revenue`]: "800px",
    [`--w-${ID}-notes`]: "200px",
  });
});

test("a path without an id, or with one that is not an identifier, sets nothing", () => {
  assert.deepEqual(run('{"name":320}', { pathname: "/sheets" }), {});
  assert.deepEqual(run('{"name":320}', { pathname: "/agents/01ARZ3NDEKTSV4RRFFQ69G5FAV" }), {});
  assert.deepEqual(run('{"name":320}', { opts: { ...options, path: "^/sheets/(.+)$" }, pathname: "/sheets/a b" }), {});
});

test("nothing stored, garbage, a throwing storage, or a value that is not a width sets nothing", () => {
  assert.deepEqual(run(null), {});
  assert.deepEqual(run("not json"), {});
  assert.deepEqual(run("[1,2]"), {});
  assert.deepEqual(run('{"name":"wide","domain":null,"city":1e999}'), {});
  assert.deepEqual(run('{"name":320}', { throwing: true }), {});
});

test("a key that is not a plain identifier is skipped", () => {
  assert.deepEqual(run('{"na me":320,"a;b":320,"ok":320}'), { [`--w-${ID}-ok`]: "320px" });
});

test("a prefix or path holding script-closing text still reads its own entry", () => {
  const hostile = { ...options, storageKeyPrefix: '</script><script>"', path: '^/sheets/([0-9A-Z]{26})$</script>' };
  assert.ok(!storedWidthsScript(hostile).includes("</script>"));
  const stored = { getItem: (key: string) => (key === `${hostile.storageKeyPrefix}${ID}` ? '{"name":320}' : null) };
  const set: Record<string, string> = {};
  const document = { documentElement: { style: { setProperty: (name: string, value: string) => void (set[name] = value) } } };
  const source = storedWidthsScript({ ...hostile, path: options.path });
  const script = new Function("localStorage", "document", "location", source);
  script(stored, document, { pathname: `/sheets/${ID}` });
  assert.deepEqual(set, { [`--w-${ID}-name`]: "320px" });
});

test("a property prefix that is not a custom property name, or a bad range, is refused", () => {
  assert.throws(() => storedWidthsScript({ ...options, propertyPrefix: "w-" }));
  assert.throws(() => storedWidthsScript({ ...options, propertyPrefix: '--w-"' }));
  assert.throws(() => storedWidthsScript({ ...options, min: 3, max: 2 }));
  assert.throws(() => storedWidthsScript({ ...options, min: Number.NaN }));
});

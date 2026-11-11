import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_SCOPE_ROWS, defaultScopeKind, effectiveRows, parseScopeRows } from "./fill-scope.ts";

test("the default selection is First 32 only past the default", () => {
  assert.equal(defaultScopeKind(0), "all");
  assert.equal(defaultScopeKind(DEFAULT_SCOPE_ROWS), "all");
  assert.equal(defaultScopeKind(DEFAULT_SCOPE_ROWS + 1), "first");
});

test("authored N clamps up to 1 and only emptiness is unparseable", () => {
  assert.equal(parseScopeRows("32"), 32);
  assert.equal(parseScopeRows(" 7 "), 7);
  assert.equal(parseScopeRows("0"), 1);
  assert.equal(parseScopeRows("-5"), 1);
  assert.equal(parseScopeRows(""), null);
  assert.equal(parseScopeRows("abc"), null);
});

test("the effective count is min(N, rowCount) and All is the sheet", () => {
  assert.equal(effectiveRows({ kind: "first", n: 32 }, 2343), 32);
  assert.equal(effectiveRows({ kind: "first", n: 32 }, 12), 12);
  assert.equal(effectiveRows({ kind: "all" }, 2343), 2343);
});

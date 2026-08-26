import assert from "node:assert/strict";
import { test } from "node:test";

import { insertVariable, isVariableRoot, promptVariables, stripVariable, usesVariable } from "./template.ts";

test("only closed expressions extract, roots deduped in order", () => {
  assert.deepEqual(promptVariables("{{name}} {{ domain|upper }} {{name}} typing {{na"), ["name", "domain"]);
});

test("strip removes every spelling of its variable and nothing else", () => {
  assert.equal(stripVariable("Find {{ name }} and {{ name|upper }} at {{domain}}", "name"), "Find and at {{domain}}");
  assert.equal(stripVariable("{{ nameplate }} stays; {{name}} goes", "name"), "{{ nameplate }} stays; goes");
});

test("uses matches any spelling, never a longer root", () => {
  assert.equal(usesVariable("{{ name|default:'x' }}", "name"), true);
  assert.equal(usesVariable("{{ nameplate }}", "name"), false);
});

test("insert appends a tight token with the spacing rule", () => {
  assert.equal(insertVariable("", "name"), "{{name}}");
  assert.equal(insertVariable("Find ", "name"), "Find {{name}}");
  assert.equal(insertVariable("Find", "name"), "Find {{name}}");
});

test("roots are case-sensitive, mirroring Django's context keys", () => {
  // {{Company}} must surface a bench input named Company; silently
  // ignoring it rendered blank server-side with no input to fill.
  assert.deepEqual(promptVariables("{{Company}} vs {{company}}"), ["Company", "company"]);
  assert.equal(usesVariable("{{Company}}", "company"), false);
});

test("token removal never eats authored leading whitespace", () => {
  assert.equal(stripVariable("{{a}} rest", "a"), "rest");
  assert.equal(stripVariable("  keep {{a}}", "a"), "  keep");
});

test("underscore-led and digit-led tokens are never inputs", () => {
  // Django refuses _x at parse, and {{2024}} is a NUMERIC LITERAL
  // that renders as itself; an input for either misleads.
  assert.deepEqual(promptVariables("{{_private}} {{2024}} {{ok_name}}"), ["ok_name"]);
});

test("every consumer judges roots through the ONE predicate", () => {
  assert.equal(isVariableRoot("company"), true);
  assert.equal(isVariableRoot("2024"), false);
  assert.equal(isVariableRoot("_x"), false);
  // A non-root key is inert on EVERY side, never a silent literal.
  assert.equal(usesVariable("{{2024}}", "2024"), false);
  assert.equal(stripVariable("keep {{2024}}", "2024"), "keep {{2024}}");
  assert.equal(insertVariable("keep", "2024"), "keep");
  // Non-roots short-circuit before the removal regex ever builds
  // (the regex also escapes its key, pure defense behind this).
  assert.equal(stripVariable("{{a}} x", "a.b"), "{{a}} x");
});

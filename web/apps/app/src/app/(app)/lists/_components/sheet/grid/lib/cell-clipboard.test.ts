import assert from "node:assert/strict";
import { test } from "node:test";

import { UNKNOWN_COLUMN_KIND, type ListColumn } from "@bower/api";

import { acceptsPaste, copiedValueFor } from "./cell-clipboard.ts";

const plain: ListColumn = { key: "name", label: "Name", type: "text", kind: "plain" };
const ai: ListColumn = { key: "a", label: "A", type: "text", kind: "ai", node_id: "n", current_fill_id: "" };
const webhook: ListColumn = { key: "w", label: "W", type: "text", kind: "webhook", node_id: "n" };
const unknown: ListColumn = { key: "u", label: "U", type: "text", kind: UNKNOWN_COLUMN_KIND };

test("a plain or AI cell copies its value as it reads, empty included", () => {
  assert.equal(copiedValueFor(plain, "Acme"), "Acme");
  assert.equal(copiedValueFor(ai, ""), "");
});

test("a webhook cell has nothing to copy and takes no paste", () => {
  assert.equal(copiedValueFor(webhook, "sent"), null);
  assert.equal(acceptsPaste(webhook), false);
  assert.equal(acceptsPaste(plain), true);
  assert.equal(acceptsPaste(ai), true);
});

test("a kind this build does not know takes no paste and copies nothing", () => {
  // Named kinds, not all-but-webhook: FAILS if the predicate goes
  // back to a deny-list, which would offer Paste on a column the
  // build cannot name.
  assert.equal(acceptsPaste(unknown), false);
  assert.equal(copiedValueFor(unknown, "x"), null);
});

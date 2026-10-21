import assert from "node:assert/strict";
import { test } from "node:test";

import { COLUMN_TYPES, type ListColumn } from "@bower/api";

import { COLUMN_TYPE_META } from "../../../../../_components/column-type-meta.ts";
import { COLUMN_ICONS, columnIconName } from "./column-icon.ts";

test("every value type the contract names has an icon, the same one its Add column entry shows", () => {
  for (const type of COLUMN_TYPES) {
    const column: ListColumn = { key: type, label: type, type, kind: "plain" };
    assert.equal(COLUMN_ICONS[columnIconName(column)], COLUMN_TYPE_META[type].icon, `the ${type} type`);
  }
});

test("a filled column's kind names its icon, whatever its value type", () => {
  const ai: ListColumn = { key: "a", label: "A", type: "number", kind: "ai", node_id: "n", current_fill_id: "" };
  const webhook: ListColumn = { key: "w", label: "W", type: "text", kind: "webhook", node_id: "n" };
  assert.equal(columnIconName(ai), "ai");
  assert.equal(columnIconName(webhook), "webhook");
  assert.notEqual(COLUMN_ICONS.ai, COLUMN_ICONS.number);
});

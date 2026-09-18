import assert from "node:assert/strict";
import { test } from "node:test";

import { columnKind, exportableColumns } from "./column-kind.ts";

const FILL = { node_id: "01NODE", current_fill_id: "" };
const WEBHOOK = { node_id: "01HOOK" };

test("a column's kind reads off its linkage, tolerantly", () => {
  assert.equal(columnKind({ fill: null, webhook: null }), "plain");
  assert.equal(columnKind({ fill: FILL, webhook: null }), "ai");
  assert.equal(columnKind({ fill: null, webhook: WEBHOOK }), "webhook");
  // A bundle from before `webhook` shipped strips the key: plain, never AI.
  assert.equal(columnKind({ fill: null } as { fill: null; webhook: null }), "plain");
  // Both present is a shape the server never writes; the tracker-backed predicate wins.
  assert.equal(columnKind({ fill: FILL, webhook: WEBHOOK }), "ai");
});

test("exportableColumns drops webhook columns only", () => {
  const columns = [
    { key: "a", fill: null, webhook: null },
    { key: "b", fill: FILL, webhook: null },
    { key: "c", fill: null, webhook: WEBHOOK },
  ];
  assert.deepEqual(
    exportableColumns(columns).map((column) => column.key),
    ["a", "b"],
  );
});

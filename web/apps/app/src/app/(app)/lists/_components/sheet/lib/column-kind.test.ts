import assert from "node:assert/strict";
import { test } from "node:test";

import { UNKNOWN_COLUMN_KIND } from "@bower/api";

import { exportableColumns } from "./column-kind.ts";

test("exportableColumns keeps the kinds that hold row data", () => {
  const columns = [
    { key: "company", kind: "plain" as const },
    { key: "answer", kind: "ai" as const },
    { key: "crm_sync", kind: "webhook" as const },
    { key: "later", kind: UNKNOWN_COLUMN_KIND },
  ];
  assert.deepEqual(
    exportableColumns(columns).map((column) => column.key),
    ["company", "answer"],
  );
});

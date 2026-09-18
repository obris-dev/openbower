import assert from "node:assert/strict";
import { test } from "node:test";

import { exportableColumns } from "./column-kind.ts";

test("exportableColumns drops webhook columns only", () => {
  const columns = [
    { key: "company", kind: "plain" as const },
    { key: "answer", kind: "ai" as const },
    { key: "crm_sync", kind: "webhook" as const },
  ];
  assert.deepEqual(
    exportableColumns(columns).map((column) => column.key),
    ["company", "answer"],
  );
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { EMPTY_COLUMN, columnKey, columnsProblem, columnsToSend } from "./columns.ts";

test("a create sends the started rows, trimmed, in order", () => {
  const drafts = [
    { label: "  Title ", type: "text" as const },
    { ...EMPTY_COLUMN },
    { label: "Link", type: "url" as const },
  ];
  assert.deepEqual(columnsToSend(drafts), [
    { label: "Title", type: "text" },
    { label: "Link", type: "url" },
  ]);
});

test("a row's field name is the server's derivation of its label", () => {
  assert.equal(columnKey({ label: "Contact Email", type: "email" }), "contact_email");
  assert.equal(columnKey({ label: "Revenue ($)", type: "currency" }), "revenue");
});

test("a custom signal declares at least one column", () => {
  // FAILS if a create with only blank rows goes out: the server refuses it,
  // and the page should say so first, where the columns are.
  assert.equal(columnsProblem([{ ...EMPTY_COLUMN }, { label: "   ", type: "text" }])?.index, 0);
  assert.equal(columnsProblem([{ label: "Title", type: "text" }]), null);
});

test("two columns that land on one field name are flagged on the second", () => {
  // FAILS if a clash goes out: the server refuses a key already taken, and
  // the row it is about should say so.
  const problem = columnsProblem([
    { label: "Title", type: "text" },
    { ...EMPTY_COLUMN },
    { label: "title!", type: "text" },
  ]);
  assert.equal(problem?.index, 2);
  assert.match(problem?.message ?? "", /"title"/);
});

test("a name with no letter or number is flagged on its row", () => {
  assert.equal(columnsProblem([{ label: "Title", type: "text" }, { label: "!!!", type: "text" }])?.index, 1);
});

test("a name the server reserves is flagged on its row", () => {
  // FAILS if "AI" goes out: it would shadow the AI column route, and the
  // server refuses it at Create.
  const problem = columnsProblem([{ label: "Title", type: "text" }, { label: "AI", type: "text" }]);
  assert.equal(problem?.index, 1);
  assert.match(problem?.message ?? "", /"AI" can't be used/);
});

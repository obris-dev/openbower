import assert from "node:assert/strict";
import { test } from "node:test";

import { curlFor, sampleValue } from "./add-rows-command.ts";

const url = "https://api.example.io/v1/sheets/01ABC/ingest";

test("the command carries one sample row keyed on every settable column", () => {
  const command = curlFor(url, [
    { key: "domain", label: "Domain", type: "url" },
    { key: "arr", label: "ARR", type: "currency" },
    { key: "founded", label: "Founded", type: "date" },
  ]);
  assert.ok(command.startsWith(`curl -X POST ${url} \\`));
  assert.ok(command.includes('-H "Authorization: Bearer $OPENBOWER_API_KEY"'));
  const body = JSON.parse(command.slice(command.indexOf("-d '") + 4, -1)) as { rows: Record<string, string>[] };
  assert.deepEqual(body, { rows: [{ domain: "https://acme.com", arr: "1200", founded: "2026-01-15" }] });
});

// Completeness over the type enum is held by the sample map's Record
// type at compile time; this pins the values the shape rules accept.
test("the typed samples are the ones the server's shape rules accept", () => {
  assert.equal(sampleValue({ key: "n", label: "N", type: "number" }), "42");
  assert.equal(sampleValue({ key: "c", label: "C", type: "currency" }), "1200");
  assert.equal(sampleValue({ key: "d", label: "D", type: "date" }), "2026-01-15");
});

test("the sample row is an envelope with its external_id when the server takes one", () => {
  // FAILS if the envelope is dropped or guessed: a sender copying the
  // command would push rows a retry doubles, or a shape an older server
  // refuses.
  const columns = [{ key: "title", label: "Title", type: "text" as const }];
  const body = (command: string) => JSON.parse(command.slice(command.indexOf("-d '") + 4, -1)) as { rows: unknown[] };
  assert.deepEqual(body(curlFor(url, columns, true)).rows, [
    { external_id: "your-id-for-this-row", cells: { title: "Acme" } },
  ]);
  assert.deepEqual(body(curlFor(url, columns)).rows, [{ title: "Acme" }]);
});

test("a quote in a column key cannot break out of the shell string", () => {
  const command = curlFor(url, [{ key: "it's", label: "It", type: "text" }]);
  assert.ok(command.includes("it'\\''s"));
});

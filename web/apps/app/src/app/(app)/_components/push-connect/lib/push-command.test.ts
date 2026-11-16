import assert from "node:assert/strict";
import { test } from "node:test";

import { PUSH_CREDENTIAL, curlFor, samplePush, sampleRow, sampleValue, type Pick } from "./push-command.ts";

const url = "https://api.example.io/v1/sheets/01ABC/ingest";
const first: Pick = () => 0;

function bodyOf(command: string) {
  return JSON.parse(command.slice(command.indexOf("-d '") + 4, -1)) as {
    request_id: string;
    rows: { external_id: string; cells: Record<string, string> }[];
  };
}

test("the command carries one sample row keyed on every settable column", () => {
  const columns = [
    { key: "domain", label: "Domain", type: "url" as const },
    { key: "arr", label: "ARR", type: "currency" as const },
    { key: "founded", label: "Founded", type: "date" as const },
  ];
  const command = curlFor(url, samplePush(columns, first), PUSH_CREDENTIAL.API_KEY);
  assert.ok(command.startsWith(`curl -X POST ${url} \\`));
  assert.ok(command.includes('-H "Authorization: Bearer $OPENBOWER_API_KEY"'));
  assert.deepEqual(
    bodyOf(command).rows.map((row) => row.cells),
    [{ domain: "https://acme.com", arr: "1200", founded: "2026-01-15" }],
  );
});

// Completeness over the type enum is held by the sample map's Record
// type at compile time; this pins values the shape rules accept.
test("the typed samples are the ones the server's shape rules accept", () => {
  assert.equal(sampleValue({ key: "n", label: "N", type: "number" }), "42");
  assert.equal(sampleValue({ key: "c", label: "C", type: "currency" }), "1200");
  assert.equal(sampleValue({ key: "d", label: "D", type: "date" }), "2026-01-15");
  for (let at = 0; at < 4; at++) {
    const pick: Pick = () => at;
    assert.match(sampleValue({ key: "d", label: "D", type: "date" }, pick), /^\d{4}-\d{2}-\d{2}$/);
    assert.match(sampleValue({ key: "n", label: "N", type: "number" }, pick), /^\d+$/);
  }
});

test("each sample row carries a fresh external_id, so a second run lands a second row", () => {
  // FAILS if the id is fixed: running the command twice would be skipped
  // as a resend of the first row.
  const columns = [{ key: "title", label: "Title", type: "text" as const }];
  const one = sampleRow(columns);
  const two = sampleRow(columns);
  assert.match(one.external_id, /^row_[a-z0-9]{8}$/);
  assert.notEqual(one.external_id, two.external_id);
  assert.deepEqual(bodyOf(curlFor(url, { request_id: "req_x", rows: [one] }, PUSH_CREDENTIAL.API_KEY)).rows, [one]);
});

test("a quote in a column key cannot break out of the shell string", () => {
  const command = curlFor(url, samplePush([{ key: "it's", label: "It", type: "text" }], first), PUSH_CREDENTIAL.API_KEY);
  assert.ok(command.includes("it'\\''s"));
});

test("a webhook URL's command carries no key: the URL is the credential", () => {
  // FAILS if the Bearer line rides a webhook URL's command: a sender would
  // look for a key the URL does not need.
  const push = samplePush([{ key: "title", label: "Title", type: "text" }], first);
  const command = curlFor(url, push, PUSH_CREDENTIAL.IN_URL);
  assert.ok(!command.includes("Authorization"));
  assert.ok(command.includes('-H "Content-Type: application/json"'));
  assert.deepEqual(bodyOf(command), push);
});

test("each sample push carries a fresh request_id, shown at the envelope's top", () => {
  // FAILS if the sample leaves request_id out or fixes it: the command is
  // the example of how a retry is made safe.
  const columns = [{ key: "title", label: "Title", type: "text" as const }];
  const one = samplePush(columns);
  const two = samplePush(columns);
  assert.match(one.request_id, /^req_[a-z0-9]{8}$/);
  assert.notEqual(one.request_id, two.request_id);
  assert.equal(bodyOf(curlFor(url, one, PUSH_CREDENTIAL.API_KEY)).request_id, one.request_id);
});

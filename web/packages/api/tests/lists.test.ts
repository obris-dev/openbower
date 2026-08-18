// addListRows: pinned here because no shipped UI posts rows yet; the
// contract (route, body shape, RowsAdded parse) must not rot silently.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { addListRows } from "../src/lists.ts";

test("addListRows posts rows and parses the RowsAdded receipt", async (t) => {
  const calls: { url: string; init: RequestInit }[] = [];
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ added: 2, row_count: 7 }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const res = await addListRows("01AAAAAAAAAAAAAAAAAAAAAAAA", [{ a: "1" }, { a: "2" }]);
  assert.equal(res.status, "ok");
  if (res.status === "ok") assert.deepEqual(res.data, { added: 2, row_count: 7 });
  assert.equal(calls.length, 1);
  assert.ok(calls[0]!.url.endsWith("/v1/lists/01AAAAAAAAAAAAAAAAAAAAAAAA/rows"));
  assert.equal(calls[0]!.init.method, "POST");
  assert.equal(calls[0]!.init.credentials, "include");
  assert.deepEqual(JSON.parse(String(calls[0]!.init.body)), { rows: [{ a: "1" }, { a: "2" }] });
});

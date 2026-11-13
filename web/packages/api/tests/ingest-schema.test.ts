// The push schema a sheet answers: its columns read tolerantly, and the
// external id flag only as the server states it.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { fetchIngestSchema } from "../src/index.ts";

function stub(t: { after: (fn: () => void) => void }, body: unknown) {
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })) as typeof fetch;
}

const COLUMNS = [{ key: "title", label: "Title", type: "text" }];

test("the external id flag reads as the server states it", async (t) => {
  stub(t, { columns: COLUMNS, external_id: true });
  const res = await fetchIngestSchema("s1");
  assert.equal(res.status === "ok" && res.data.external_id, true);
});

test("a server from before external ids states none, so rows stay plain", async (t) => {
  // FAILS if a missing flag defaults on: a command sending envelopes to a
  // server that reads them as unknown columns would be refused.
  stub(t, { columns: COLUMNS });
  const res = await fetchIngestSchema("s1");
  assert.equal(res.status === "ok" && res.data.external_id, false);
});

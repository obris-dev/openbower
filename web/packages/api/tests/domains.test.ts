// The domain-normalization agreement vectors: the Python side runs the
// same fixture (common/tests/test_domains.py), so divergence fails one
// suite or the other. Runs under node's native type stripping.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { normalizeDomain } from "../src/domains.ts";

const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../../../packages/kernel/fixtures/domain_normalization.json"), "utf8"),
) as { vectors: { input: string; expected: string }[] };

test("normalizeDomain matches the shared agreement vectors", () => {
  for (const { input, expected } of fixture.vectors) {
    assert.equal(normalizeDomain(input), expected, JSON.stringify(input));
  }
});

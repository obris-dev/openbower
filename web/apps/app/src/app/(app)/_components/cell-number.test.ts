import assert from "node:assert/strict";
import { test } from "node:test";

import { cellNumber } from "./cell-number.ts";

test("a stored amount reads with its whole part grouped", () => {
  // FAILS if a funding amount still reads as a run of digits.
  assert.equal(cellNumber("currency", "47000000", "en-US"), "47,000,000");
  assert.equal(cellNumber("currency", "$1250000.50", "en-US"), "$1,250,000.50");
  assert.equal(cellNumber("number", "-12500", "en-US"), "-12,500");
  assert.equal(cellNumber("number", "999", "en-US"), "999");
});

test("grouping never rounds or drops a digit", () => {
  // FAILS if the value goes through a float: both lose digits there.
  assert.equal(cellNumber("number", "12345678901234567890", "en-US"), "12,345,678,901,234,567,890");
  assert.equal(cellNumber("number", "0.123456789012345678", "en-US"), "0.123456789012345678");
});

test("the reader's locale groups and marks the fraction", () => {
  assert.equal(cellNumber("currency", "€1250000.5", "de-DE"), "€1.250.000,5");
});

test("any other form, or any other type, renders as stored", () => {
  // FAILS if a value the server's rule did not write is reformatted.
  assert.equal(cellNumber("currency", "47,000,000", "en-US"), null);
  assert.equal(cellNumber("currency", "47M", "en-US"), null);
  assert.equal(cellNumber("number", "$5", "en-US"), null);
  assert.equal(cellNumber("text", "47000000", "en-US"), null);
  assert.equal(cellNumber("date", "20261119", "en-US"), null);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { ETA_MAX_SAMPLES, etaSeconds, formatEta, pushSample, type EtaSample } from "./fill-eta.ts";

test("samples accumulate only on progress and stay windowed", () => {
  let samples: EtaSample[] = [];
  samples = pushSample(samples, 1_000, 0);
  samples = pushSample(samples, 2_000, 0);
  assert.equal(samples.length, 1);
  samples = pushSample(samples, 3_000, 4);
  assert.equal(samples.length, 2);
  for (let n = 0; n < 40; n += 1) samples = pushSample(samples, 4_000 + n * 1_000, 5 + n);
  assert.equal(samples.length, ETA_MAX_SAMPLES);
});

test("eta needs two samples and real progress", () => {
  assert.equal(etaSeconds([], 100), null);
  assert.equal(etaSeconds([{ at: 1_000, attempted: 1 }], 100), null);
  const stalled = [
    { at: 1_000, attempted: 5 },
    { at: 61_000, attempted: 5 },
  ];
  assert.equal(etaSeconds(stalled, 100), null);
  assert.equal(
    etaSeconds(
      [
        { at: 0, attempted: 0 },
        { at: 60_000, attempted: 6 },
      ],
      100,
    ),
    // 6 rows per minute observed, 100 remaining: 1000 seconds.
    1_000,
  );
  assert.equal(etaSeconds([{ at: 0, attempted: 0 }, { at: 60_000, attempted: 6 }], 0), null);
});

test("format speaks one approximate unit pair", () => {
  assert.equal(formatEta(40), "~1 min");
  assert.equal(formatEta(720), "~12 min");
  assert.equal(formatEta(7_800), "~2 h 10 min");
  assert.equal(formatEta(7_200), "~2 h");
});

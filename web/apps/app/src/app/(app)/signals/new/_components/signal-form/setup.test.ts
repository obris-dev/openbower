// The setup a Test tries and Create saves: the filter the form says, the
// judge only with a prompt, and the gaps each attempt diagnoses.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { PREVIEW_OUTCOME, type FundingStage, type PreviewRow } from "@bower/api";

import { AMOUNT_INVALID_GAP, AMOUNT_LABEL, matchesTitle, AMOUNT_REVERSED_GAP, STALE_FILTER, TEST_STALE } from "./copy.ts";
import {
  ATTEMPT,
  attemptVerdict,
  checklistFor,
  fundingFilterFrom,
  gapsOf,
  groupRows,
  keepsEveryRound,
  nameOf,
  suggestedName,
  rowAt,
  setupFrom,
  setupKey,
  staleNote,
  thresholdFrom,
  type SignalFormState,
} from "./setup.ts";

const EMPTY: SignalFormState = {
  label: "",
  labelEdited: false,
  stages: new Set<FundingStage>(),
  minAmount: "",
  maxAmount: "",
  prompt: "",
  minConfidence: 0.8,
};

test("an empty form keeps every raise", () => {
  assert.deepEqual(fundingFilterFrom(EMPTY), {});
});

test("stages go in the contract's order, whatever order they were checked", () => {
  const filter = fundingFilterFrom({ ...EMPTY, stages: new Set<FundingStage>(["series_a", "pre_seed"]) });
  assert.deepEqual(filter.stages, ["pre_seed", "series_a"]);
});

test("a bound carries the currency, and no bound sends none", () => {
  // FAILS if the currency rides without a range: the service refuses a
  // currency with nothing to compare.
  assert.deepEqual(fundingFilterFrom({ ...EMPTY, minAmount: "1,000,000" }), {
    min_amount: 1_000_000,
    currency: "USD",
  });
  assert.equal("currency" in fundingFilterFrom(EMPTY), false);
});

test("the judge rides only with a prompt", () => {
  assert.deepEqual(setupFrom({ ...EMPTY, minConfidence: 0.9 }), { filter: {} });
  assert.deepEqual(setupFrom({ ...EMPTY, prompt: "  B2B software  ", minConfidence: 0.9 }), {
    filter: {},
    prompt: "B2B software",
    min_confidence: 0.9,
  });
});

test("an amount that is not a number above zero, or a reversed range, is a gap", () => {
  assert.equal(gapsOf({ ...EMPTY, minAmount: "5m" }).amount, AMOUNT_INVALID_GAP);
  assert.equal(gapsOf({ ...EMPTY, maxAmount: "0" }).amount, AMOUNT_INVALID_GAP);
  assert.equal(gapsOf({ ...EMPTY, minAmount: "10", maxAmount: "5" }).amount, AMOUNT_REVERSED_GAP);
  assert.equal(gapsOf({ ...EMPTY, minAmount: "5", maxAmount: "5" }).amount, null);
});

test("a checklist item reads as the title of the section it points at", () => {
  const amount = checklistFor(ATTEMPT.TEST, gapsOf(EMPTY))[0];
  assert.equal(amount?.label, AMOUNT_LABEL);
});

test("Test needs no name; Create does", () => {
  const gaps = gapsOf({ ...EMPTY, labelEdited: true });
  assert.deepEqual(
    checklistFor(ATTEMPT.TEST, gaps).map((item) => item.key),
    ["amount"],
  );
  assert.deepEqual(
    checklistFor(ATTEMPT.CREATE, gaps).filter((item) => item.missing).map((item) => item.key),
    ["label"],
  );
});

function judged(outcome: PreviewRow["outcome"], confidence: number | null): PreviewRow {
  return { outcome, confidence, reason: "", error: null, event: {} as PreviewRow["event"] };
}

test("a judged yes re-sorts at any threshold, and nothing else moves", () => {
  // FAILS if a no or a filtered-out event could become a match by moving
  // the threshold: only the judge's yes carries a threshold.
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.UNSURE, 0.7), 0.65).outcome, PREVIEW_OUTCOME.MATCHED);
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.MATCHED, 0.7), 0.75).outcome, PREVIEW_OUTCOME.UNSURE);
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.MATCHED, 0.75), 0.75).outcome, PREVIEW_OUTCOME.MATCHED);
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.UNMATCHED, 0.9), 0.5).outcome, PREVIEW_OUTCOME.UNMATCHED);
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.PASSED, null), 0.5).outcome, PREVIEW_OUTCOME.PASSED);
  assert.equal(rowAt(judged(PREVIEW_OUTCOME.MATCHED, null), 0.95).outcome, PREVIEW_OUTCOME.MATCHED);
});

test("moving the threshold leaves a Test current; changing the prompt does not", () => {
  const prompted = { ...EMPTY, prompt: "B2B" };
  assert.equal(setupKey(setupFrom(prompted)), setupKey(setupFrom({ ...prompted, minConfidence: 0.6 })));
  assert.notEqual(setupKey(setupFrom(prompted)), setupKey(setupFrom({ ...prompted, prompt: "B2C" })));
});

test("a slider position reads as whole percents", () => {
  assert.equal(thresholdFrom(0.8500000000000001), 0.85);
});

test("the name is suggested from the setup until the person edits it", () => {
  assert.equal(suggestedName(EMPTY), "Funding rounds");
  const seedAndA = { ...EMPTY, stages: new Set<FundingStage>(["series_a", "seed"]) };
  assert.equal(suggestedName({ ...seedAndA, minAmount: "5000000" }), "Seed and Series A rounds, $5M+");
  assert.equal(suggestedName({ ...EMPTY, maxAmount: "25,000,000" }), "Funding rounds, up to $25M");
  assert.equal(
    suggestedName({ ...EMPTY, stages: new Set<FundingStage>(["seed", "series_a", "series_b"]), minAmount: "1000000", maxAmount: "5000000" }),
    "Seed, Series A and Series B rounds, $1M to $5M",
  );
  assert.equal(nameOf({ ...seedAndA, label: "  Mine ", labelEdited: true }), "Mine");
  // An edited name cleared to blank is a gap, not a fallback to the
  // suggestion the person replaced.
  assert.equal(gapsOf({ ...EMPTY, labelEdited: true }).label, true);
  assert.equal(gapsOf(EMPTY).label, false);
});

test("a setup that checks nothing keeps every round", () => {
  assert.equal(keepsEveryRound(setupFrom(EMPTY)), true);
  assert.equal(keepsEveryRound(setupFrom({ ...EMPTY, prompt: "B2B" })), false);
  assert.equal(keepsEveryRound(setupFrom({ ...EMPTY, maxAmount: "5" })), false);
});

test("rows group by what the signal would do with them, newest first in each", () => {
  // FAILS if an unknown outcome lands among the matches: it claims nothing.
  const rows = [
    judged(PREVIEW_OUTCOME.PASSED, null),
    judged(PREVIEW_OUTCOME.MATCHED, 0.9),
    judged(PREVIEW_OUTCOME.UNSURE, 0.6),
    judged(PREVIEW_OUTCOME.WAITING, null),
    judged("unknown_outcome", null),
    judged(PREVIEW_OUTCOME.MATCHED, 0.95),
  ];
  const groups = groupRows(rows);
  assert.deepEqual(groups.matches, [rows[1], rows[5]]);
  assert.deepEqual(groups.unsure, [rows[2]]);
  assert.deepEqual(groups.checking, [rows[3]]);
  assert.deepEqual(groups.notKept, [rows[0], rows[4]]);
});

test("results say they are out of date only when nothing will update them", () => {
  // FAILS if the filter-only line shows while the auto-preview is about to
  // run (it flashed on every edit) or for an amount that reads fine.
  const quiet = { stale: true, updating: false, failed: false, prompted: false, amountInvalid: false };
  assert.equal(staleNote(quiet), null);
  assert.equal(staleNote({ ...quiet, amountInvalid: true }), STALE_FILTER);
  assert.equal(staleNote({ ...quiet, amountInvalid: true, updating: true }), null);
  assert.equal(staleNote({ ...quiet, amountInvalid: true, failed: true }), null);
  assert.equal(staleNote({ ...quiet, prompted: true }), TEST_STALE);
  assert.equal(staleNote({ ...quiet, prompted: true, stale: false }), null);
});

test("only a blocked attempt is kept, so a passing one clears the diagnosis", () => {
  // FAILS if a clean attempt is kept: a later slip would be flagged at
  // its field and in the footer before anything was tried.
  assert.deepEqual(attemptVerdict(ATTEMPT.TEST, gapsOf(EMPTY)), { attempted: null, jumpTo: null });
  const blocked = attemptVerdict(ATTEMPT.TEST, gapsOf({ ...EMPTY, minAmount: "5m" }));
  assert.equal(blocked.attempted, ATTEMPT.TEST);
  assert.notEqual(blocked.jumpTo, null);
});

test("no matches is claimed only once every round was decided, of the rounds tried", () => {
  // FAILS if it is claimed while rounds are still being read, or counts
  // the request's cap rather than the rounds that came back.
  assert.equal(matchesTitle(0, 12, 0), "No matches in the 12 newest rounds");
  assert.equal(matchesTitle(0, 12, 3), "Matches (0)");
  assert.equal(matchesTitle(4, 12, 0), "Matches (4)");
});

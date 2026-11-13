// The form's state as the wire takes it, and what an attempt still needs.
// Pure, so the setup a Test tries and the one Create saves are one
// derivation, tested here.
import {
  FUNDING_STAGES,
  PREVIEW_OUTCOME,
  type FundingFilter,
  type FundingStage,
  type PreviewRow,
  type SignalSetup,
} from "@bower/api";

import type { ChecklistItem } from "../../../../_components/readiness/section";
import {
  AMOUNT_CHECK,
  AMOUNT_INVALID_GAP,
  AMOUNT_REVERSED_GAP,
  NAME_CHECK,
  STAGE_LABEL,
  STALE_FILTER,
  TEST_STALE,
} from "./copy.ts";
import { compactAmount } from "./format.ts";

export type SignalFormState = {
  /** What the person typed; read only once they edited the name. */
  label: string;
  labelEdited: boolean;
  stages: ReadonlySet<FundingStage>;
  minAmount: string;
  maxAmount: string;
  prompt: string;
  minConfidence: number;
};

/** The amount range's currency. Amounts compare only within a currency
 * (the service converts nothing), so a range is in US dollars and keeps
 * only rounds reported in them. */
export const AMOUNT_CURRENCY = "USD";

export const ATTEMPT = { TEST: "test", CREATE: "create" } as const;
export type Attempt = (typeof ATTEMPT)[keyof typeof ATTEMPT];

// The anchors a gap jumps to.
export const NAME_ID = "signal-label";
export const AMOUNT_ID = "signal-amount";

const AMOUNT_GAP = { INVALID: "invalid", REVERSED: "reversed" } as const;
type AmountGap = (typeof AMOUNT_GAP)[keyof typeof AMOUNT_GAP];

/** An amount as typed: null when blank, NaN when it is not a number above
 * zero. Thousands separators are allowed. */
function parseAmount(raw: string): number | null {
  const trimmed = raw.trim().replaceAll(",", "");
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value > 0 ? value : Number.NaN;
}

function amountGap(state: SignalFormState): AmountGap | null {
  const min = parseAmount(state.minAmount);
  const max = parseAmount(state.maxAmount);
  if (Number.isNaN(min) || Number.isNaN(max)) return AMOUNT_GAP.INVALID;
  if (min !== null && max !== null && min > max) return AMOUNT_GAP.REVERSED;
  return null;
}

/** The funding filter the form says. Stages in the contract's order;
 * a range carries the currency and no range sends none, so the service's
 * "a range and its currency go together" holds by construction. */
export function fundingFilterFrom(state: SignalFormState): Partial<FundingFilter> {
  const filter: Partial<FundingFilter> = {};
  const stages = FUNDING_STAGES.filter((stage) => state.stages.has(stage));
  if (stages.length > 0) filter.stages = stages;
  const min = parseAmount(state.minAmount);
  const max = parseAmount(state.maxAmount);
  if (min !== null) filter.min_amount = min;
  if (max !== null) filter.max_amount = max;
  if (min !== null || max !== null) filter.currency = AMOUNT_CURRENCY;
  return filter;
}

/** Why the results on screen are not the setup's, or null when there is
 * nothing to say: they are current, an update is on its way (a pending
 * or running preview), or the last try failed (its notice says why). A
 * described setup waits for Test; a filter-only one updates itself, so
 * it is only ever held back by an amount that does not read. */
export function staleNote(facts: {
  stale: boolean;
  updating: boolean;
  failed: boolean;
  prompted: boolean;
  amountInvalid: boolean;
}): string | null {
  if (!facts.stale || facts.updating || facts.failed) return null;
  if (facts.prompted) return TEST_STALE;
  return facts.amountInvalid ? STALE_FILTER : null;
}

/** A test's rows as the panel shows them: what the signal would bring
 * first, then the rounds the threshold decides, the ones still being
 * read, and the rest. Each keeps the run's newest-first order. */
export type RowGroups = { matches: PreviewRow[]; unsure: PreviewRow[]; checking: PreviewRow[]; notKept: PreviewRow[] };

export function groupRows(rows: PreviewRow[]): RowGroups {
  const groups: RowGroups = { matches: [], unsure: [], checking: [], notKept: [] };
  for (const row of rows) {
    if (row.outcome === PREVIEW_OUTCOME.MATCHED) groups.matches.push(row);
    else if (row.outcome === PREVIEW_OUTCOME.UNSURE) groups.unsure.push(row);
    else if (row.outcome === PREVIEW_OUTCOME.WAITING) groups.checking.push(row);
    else groups.notKept.push(row);
  }
  return groups;
}

/** The name the setup suggests, like "Seed and Series A rounds, $5M+". */
export function suggestedName(state: SignalFormState): string {
  const stages = FUNDING_STAGES.filter((stage) => state.stages.has(stage)).map((stage) => STAGE_LABEL[stage]);
  const lead = stages.length === 0 ? "Funding" : joinWithAnd(stages);
  const range = rangeLabel(state);
  return range === null ? `${lead} rounds` : `${lead} rounds, ${range}`;
}

function joinWithAnd(words: string[]): string {
  if (words.length <= 2) return words.join(" and ");
  return `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

function rangeLabel(state: SignalFormState): string | null {
  if (amountGap(state) !== null) return null;
  const min = parseAmount(state.minAmount);
  const max = parseAmount(state.maxAmount);
  if (min !== null && max !== null) return `${compactAmount(min, AMOUNT_CURRENCY)} to ${compactAmount(max, AMOUNT_CURRENCY)}`;
  if (min !== null) return `${compactAmount(min, AMOUNT_CURRENCY)}+`;
  if (max !== null) return `up to ${compactAmount(max, AMOUNT_CURRENCY)}`;
  return null;
}

/** The name Create saves: the person's once they edited it, else the
 * suggestion. */
export function nameOf(state: SignalFormState): string {
  return state.labelEdited ? state.label.trim() : suggestedName(state);
}

/** Whether the setup checks nothing, so every round matches. */
export function keepsEveryRound(setup: SignalSetup): boolean {
  return Object.keys(setup.filter).length === 0 && setup.prompt === undefined;
}

/** The setup a Test tries and Create saves: the filter, and the judge's
 * prompt with its threshold when there is a prompt. */
export function setupFrom(state: SignalFormState): SignalSetup {
  const filter = fundingFilterFrom(state);
  const prompt = state.prompt.trim();
  if (prompt === "") return { filter };
  return { filter, prompt, min_confidence: state.minConfidence };
}

/** What a Test's results depend on: the filter and the prompt. The
 * threshold is left out, since results are re-sorted at any threshold
 * without testing again. */
export function setupKey(setup: SignalSetup): string {
  return JSON.stringify({ filter: setup.filter, prompt: setup.prompt ?? "" });
}

/** The judge's threshold: a slider in 5% steps, down to half sure or the
 * catalog's default when that is lower. */
export const THRESHOLD_MAX = 1;
export const THRESHOLD_STEP = 0.05;
const THRESHOLD_FLOOR = 0.5;
export function thresholdMin(defaultValue: number): number {
  return Math.min(THRESHOLD_FLOOR, defaultValue);
}

/** A slider position as a threshold: whole percents, so a step's float
 * drift never decides a match. */
export function thresholdFrom(raw: number): number {
  return Math.round(raw * 100) / 100;
}

/** A tested row at another threshold, by the contract's own rule
 * (`PreviewRowWire.outcome`): a judge's yes at or above the threshold
 * matches, a yes below it is unsure. A no, an event outside the filter,
 * and one not judged stay as they are, so moving the threshold never
 * calls the judge again. */
export function rowAt(row: PreviewRow, threshold: number): PreviewRow {
  const saidYes = row.outcome === PREVIEW_OUTCOME.MATCHED || row.outcome === PREVIEW_OUTCOME.UNSURE;
  if (!saidYes || row.confidence === null) return row;
  const outcome = row.confidence >= threshold ? PREVIEW_OUTCOME.MATCHED : PREVIEW_OUTCOME.UNSURE;
  return { ...row, outcome };
}

export type Gaps = { label: boolean; amount: string | null };

/** What the form still needs, as the copy each gap shows at its field. */
export function gapsOf(state: SignalFormState): Gaps {
  const amount = amountGap(state);
  return {
    label: nameOf(state) === "",
    amount: amount === AMOUNT_GAP.INVALID ? AMOUNT_INVALID_GAP : amount === AMOUNT_GAP.REVERSED ? AMOUNT_REVERSED_GAP : null,
  };
}

/** The attempt's checklist: Test needs no name, Create does. */
/** An attempt's verdict: the gap to take the person to, and the attempt
 * the form keeps diagnosing. Only a blocked attempt is kept; one that goes
 * through clears it, so a later slip is not flagged before it is tried. */
export function attemptVerdict(attempt: Attempt, gaps: Gaps): { attempted: Attempt | null; jumpTo: string | null } {
  const first = checklistFor(attempt, gaps).find((item) => item.missing);
  if (!first) return { attempted: null, jumpTo: null };
  return { attempted: attempt, jumpTo: first.anchor };
}

/** The sections an attempt here can need. */
export type SignalChecklistItem = ChecklistItem<"label" | "amount">;

export function checklistFor(attempt: Attempt, gaps: Gaps): SignalChecklistItem[] {
  const amount: SignalChecklistItem = { key: "amount", anchor: AMOUNT_ID, label: AMOUNT_CHECK, missing: gaps.amount !== null };
  if (attempt === ATTEMPT.TEST) return [amount];
  return [{ key: "label", anchor: NAME_ID, label: NAME_CHECK, missing: gaps.label }, amount];
}

// The signal form's copy, one place: Create signal and a signal's settings.
import {
  PREVIEW_OUTCOME,
  UNKNOWN_PREVIEW_OUTCOME,
  type FundingStage,
  type PreviewOutcome,
  type PreviewRow,
  type PreviewRun,
} from "@bower/api";

export const TITLE = "Create signal";
export const INTRO = "Get new funding rounds that match who you sell to.";
export const CREATE_ACTION = "Create signal";
export const TEST_ACTION = "Test";
export const CREATED_TOAST = "Signal created.";
// Editing a saved signal: the same form, its own verbs.
export const SAVE_ACTION = "Save";
export const SAVED_TOAST = "Signal saved.";
export const UNSAVED = "Unsaved changes | Save keeps them.";

// The sections, as the signal works: the fixed checks, the optional
// judgment, then its name.
export const WHICH_ROUNDS_TITLE = "Which rounds";
export const WHAT_FITS_TITLE = "What fits";
export const OPTIONAL = "Optional";

export const TYPE_LABEL = "Type";
export const STAGES_LABEL = "Stage";
export const STAGES_HINT = "None checked keeps every stage.";
export const AMOUNT_LABEL = "Amount (USD)";
export const AMOUNT_HINT = "Leave blank for any amount. A range keeps only rounds reported in US dollars.";
export const MIN_AMOUNT_LABEL = "Minimum amount";
export const MAX_AMOUNT_LABEL = "Maximum amount";
export const AMOUNT_TO = "to";
export const PROMPT_LABEL = "Describe the companies you want";
export const PROMPT_HINT = "Each round that passes the checks above is read against this, and only the ones that fit are kept.";
export const PROMPT_PLACEHOLDER = "B2B software sold to finance teams";
export const THRESHOLD_LABEL = "Minimum confidence";
export const NAME_TITLE = "Name";
export const NAME_HINT = "Suggested from your setup below. Change it if you like.";

export const NAME_GAP = "Name the signal to create it.";
export const NAME_SAVE_GAP = "Name the signal to save it.";
export const AMOUNT_INVALID_GAP = "Enter an amount as a number above zero, like 5000000.";
export const AMOUNT_REVERSED_GAP = "The lower amount is above the upper one.";
export const NAME_CHECK = "Name";
export const AMOUNT_CHECK = AMOUNT_LABEL;

/** A funding stage as a person reads it. A stage the contract adds fails
 * the typecheck here until it is named. */
export const STAGE_LABEL: Record<FundingStage, string> = {
  pre_seed: "Pre-seed",
  seed: "Seed",
  series_a: "Series A",
  series_b: "Series B",
  series_c: "Series C",
  series_d_plus: "Series D+",
  growth: "Growth",
  bridge: "Bridge",
};


export const THRESHOLD_HINT = "How clearly a round must fit what you're looking for. Lower allows looser matches; higher is stricter.";

/** A threshold as a person reads it: a whole percent. */
export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

// The preview: the newest rounds, tried against the setup.
export const RESULTS_TITLE = "Recent rounds";
export function promptedIntro(events: number): string {
  return `Test to check the ${events} newest rounds against your description.`;
}
export const TEST_READING = "Reading recent rounds…";
export const TEST_JUDGING = "checking…";
export const TEST_STALE = "From an earlier setup | Test to check these against your description.";
/** The matches' heading: "no matches" is claimed only once every round
 * was decided, and of the rounds actually tried. */
export function matchesTitle(count: number, tried: number, checking: number): string {
  if (count > 0 || checking > 0) return `Matches (${count})`;
  return `No matches in the ${tried} newest rounds`;
}
export function unsureTitle(count: number): string {
  return `Unsure (${count})`;
}
export const UNSURE_HINT = "A yes below your minimum confidence. Lower it to keep these.";
export function checkingTitle(count: number): string {
  return `Checking (${count})`;
}
export function notKeptTitle(count: number): string {
  return `Not kept (${count})`;
}
export const STALE_FILTER = "From an earlier setup | Fix the amount to update.";
export const EVERY_ROUND = "No stage, amount or description set: every funding round matches.";
export const NO_EVENTS = "No funding rounds yet to try this on.";
export const LOST_SIGHT = "This page can no longer follow the test; run it again.";

/** How many of a test's events the setup would have kept, at the
 * threshold when a prompt was judged. */
export function testSummary(rows: PreviewRow[], threshold: number | null): string {
  const matched = rows.filter((row) => row.outcome === PREVIEW_OUTCOME.MATCHED).length;
  const counted = `${matched} of ${rows.length} match`;
  return threshold === null ? counted : `${counted} at ${percent(threshold)} confidence`;
}

/** Why a test failed, in words a person can act on. */
export const FAILURE: Record<NonNullable<PreviewRun["error"]>["kind"], string> = {
  slow_down: "The judge is busy right now.",
  unavailable: "The judge could not be reached; try again.",
  deadline: "The test ran out of time; try again.",
  abandoned: "The test was stopped before it finished; run it again.",
  unexpected: "The test failed; try again.",
};
export const SLOW_DOWN_RETRY = "Try again after";

/** An outcome's pill: its words and its role, or none for an outcome
 * this bundle has not heard of (it claims nothing). Waiting has no pill;
 * its row shows a spinner. */
export const OUTCOME_PILL: Record<
  PreviewOutcome,
  { label: string; tone: "success" | "warning" | "quiet" | "faint" | "danger" } | null
> = {
  [PREVIEW_OUTCOME.WAITING]: null,
  [PREVIEW_OUTCOME.MATCHED]: { label: "Matched", tone: "success" },
  [PREVIEW_OUTCOME.UNSURE]: { label: "Unsure", tone: "warning" },
  [PREVIEW_OUTCOME.UNMATCHED]: { label: "Not a match", tone: "quiet" },
  [PREVIEW_OUTCOME.PASSED]: { label: "Filtered out", tone: "faint" },
  [PREVIEW_OUTCOME.ERRORED]: { label: "Couldn't judge", tone: "danger" },
  [UNKNOWN_PREVIEW_OUTCOME]: null,
};

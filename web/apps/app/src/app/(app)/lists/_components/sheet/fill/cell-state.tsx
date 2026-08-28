import { Popover, PopoverButton, PopoverPanel, Skeleton } from "@bower/ui";
import {
  SETTLED_CELL_STATES,
  UNKNOWN_CELL_STATE,
  type AgentCatalog,
  type CellState,
  type RenderableCellState,
  type SettledCellState,
} from "@bower/api";

/** The deployment's web-search door, when the sheet has fetched it
 * (a server fact the rate-limited cell composes; null until known). */
export type SearchDoor = AgentCatalog["search_provider"] | null;

// The blank causes in user words (the server ships the structured
// cause, this surface phrases it). The settled-vs-retryable PARTITION
// comes off the contract (SETTLED_CELL_STATES): a SETTLED cause is
// terminal under this config, so the cell speaks a quiet word instead
// of a warning dot; a RETRYABLE cause re-runs on the next fill and
// keeps the dot. The copy records below are typed against that
// partition, so a cause added server-side fails the build here
// instead of rendering an unnamed cell.
type SettledCause = SettledCellState;
type RetryableCause = Exclude<RenderableCellState, "pending" | SettledCause>;

const SETTLED_CAUSES: Record<SettledCause, { word: string; cause: string }> = {
  no_evidence: { word: "none found", cause: "No evidence found" },
  no_answer: { word: "no answer", cause: "The model spent its search budget without answering" },
  unverified: { word: "unverified", cause: "What was found couldn't be confirmed for this row" },
  unparseable: { word: "unusable", cause: "The model's answer could not be used" },
  type_mismatch: { word: "wrong type", cause: "The answer did not fit this column's type" },
};
const SETTLED_FACT = "Won't re-run on Continue; edit the prompt to try again.";

const RETRYABLE_CAUSES: Record<RetryableCause, string> = {
  no_tools_door: "No search provider is connected",
  model_error: "The model errored",
  transient: "The model provider was unavailable; retries exhausted",
  // Keyed by TOOL on the wire, because a user reads "finding
  // contacts" and "web search" as different things even though one
  // seam serves both.
  search_throttled: "Web search kept rate-limiting this row; retries exhausted",
  contacts_throttled: "Finding contacts kept rate-limiting this row; retries exhausted",
  // A cause this build has never heard of. It claims nothing about
  // WHY, because it cannot know: the server named a reason this
  // bundle predates.
  [UNKNOWN_CELL_STATE]: "This page is older than the reason given",
};
const RETRY_FACT = "Runs again on Continue.";
// The paid-door nudge is a tier-2 composition: the server ships WHICH
// door serves web search, and the sentence renders only where the
// paid door is a remedy (the free door refused). A contacts refusal
// already came from the paid door, and an unknown door claims
// nothing.
const PAID_DOOR_NUDGE = "DataForSEO (pay as you go, a deployment setting) gives dedicated throughput.";

function retryFact(state: RetryableCause, searchDoor: SearchDoor): string {
  if (state === "search_throttled" && searchDoor === "duckduckgo") return `${RETRY_FACT} ${PAID_DOOR_NUDGE}`;
  return RETRY_FACT;
}
// The retryable FACT is a promise, and an unrecognised cause cannot
// make it: if the cause the server added is a settled one, Continue
// will not re-run this cell. The dot is still the honest mark (it has
// not been shown to settle), but the sentence beside it must claim as
// little as the cause does.
const UNKNOWN_FACT = "This page is too old to say whether Continue will retry it.";

/** Every pointer's path to a cell's why: the mark (word or dot) is a
 * real disclosure button, so tap, click, and Enter all open a small
 * panel speaking the cause and its re-run fact (hover cannot exist on
 * a coarse pointer, so hover-only tooltips leave mobile mute; title
 * stays as the desktop quick peek). The Popover primitive carries the
 * floor (aria-expanded, Escape, outside-click, focus return), and its
 * panel portals to body, so the sheet's own overflow never clips it. */
function CauseMark({ cause, fact, children }: { cause: string; fact: string; children: React.ReactNode }) {
  const sentence = `${cause}. ${fact}`;
  return (
    <Popover className="flex h-5 items-center">
      <PopoverButton
        aria-label={sentence}
        title={sentence}
        className="-mx-1.5 flex h-5 min-w-5 items-center justify-center rounded px-1.5 outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-signal"
      >
        {children}
      </PopoverButton>
      <PopoverPanel anchor="bottom start" className="py-0 motion-reduce:transition-none">
        <div className="max-w-64 px-3 py-2">
          <p className="text-xs text-foreground">{cause}.</p>
          <p className="mt-0.5 text-xs text-faint">{fact}</p>
        </div>
      </PopoverPanel>
    </Popover>
  );
}

/** One AI cell's non-value state: `pending` shimmers (the Skeleton
 * primitive already honors prefers-reduced-motion), a SETTLED blank
 * speaks its quiet word in the cell (data-quiet: faint and small,
 * never louder than a real value), a RETRYABLE blank keeps the
 * warning dot. Both open the fuller sentence plus their re-run fact
 * on tap, click, focus, and to screen readers (the row drawer will
 * carry the full diagnosis). Filled cells and not-attempted rows never
 * reach here: both are the ABSENCE of a state, rendered as the plain
 * value or nothing. `searchDoor` is the deployment's web-search door
 * when the sheet has it (fetched only once a rate-limited cell is on
 * screen); the rate-limited popover composes the paid-door nudge off
 * it. */
export function AiCellState({ state, searchDoor = null }: { state: RenderableCellState; searchDoor?: SearchDoor }) {
  if (state === "pending") {
    return (
      <span className="flex h-5 items-center">
        <Skeleton className="h-3 w-20" />
        <span className="sr-only">Filling</span>
      </span>
    );
  }
  if ((SETTLED_CELL_STATES as readonly string[]).includes(state)) {
    const { word, cause } = SETTLED_CAUSES[state as SettledCause];
    return (
      <CauseMark cause={cause} fact={SETTLED_FACT}>
        <span aria-hidden className="text-xs text-faint">
          {word}
        </span>
      </CauseMark>
    );
  }
  return (
    <CauseMark
      cause={RETRYABLE_CAUSES[state as RetryableCause]}
      fact={state === UNKNOWN_CELL_STATE ? UNKNOWN_FACT : retryFact(state as RetryableCause, searchDoor)}
    >
      <span aria-hidden className="h-2 w-2 rounded-full bg-warning-edge" />
    </CauseMark>
  );
}

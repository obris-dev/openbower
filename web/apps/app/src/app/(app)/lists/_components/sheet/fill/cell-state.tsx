import { Popover, PopoverButton, PopoverPanel, Skeleton } from "@bower/ui";
import {
  SETTLED_CELL_STATES,
  UNKNOWN_CELL_STATE,
  type AgentCatalog,
  type CellState,
  type RenderableCellState,
  type RenderableCellStateWire,
  type SettledCellState,
  type ToolKey,
  type ToolStatuses,
} from "@bower/api";

/** The deployment's web-search door, when the sheet has fetched it
 * (a server fact the degraded-tool copy composes; null until known). */
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
type RetryableCause = Exclude<RenderableCellState, "pending" | "filled" | SettledCause>;

const SETTLED_CAUSES: Record<SettledCause, { word: string; cause: string }> = {
  no_evidence: { word: "none found", cause: "No evidence found" },
  no_answer: { word: "no answer", cause: "The model spent its search budget without answering" },
  unverified: { word: "unverified", cause: "What was found couldn't be confirmed for this row" },
  unparseable: { word: "unusable", cause: "The model's answer could not be used" },
  type_mismatch: { word: "wrong type", cause: "The answer did not fit this column's type" },
};
const SETTLED_FACT = "Won't re-run on Continue; edit the prompt to try again.";

// The retryable causes that are NOT a tool's doing carry one sentence
// each; the two tool_* states carry none of their own, because the
// sentence comes from the TOOL STATUSES beside the state (below).
const RETRYABLE_CAUSES: Record<Exclude<RetryableCause, "tool_not_configured" | "tool_unavailable">, string> = {
  model_error: "The model errored",
  transient: "The model provider was unavailable; retries exhausted",
  // A cause this build has never heard of. It claims nothing about
  // WHY, because it cannot know: the server named a reason this
  // bundle predates.
  [UNKNOWN_CELL_STATE]: "This page is older than the reason given",
};
const RETRY_FACT = "Runs again on Continue.";
const NOT_CONFIGURED_FACT = "Runs again on Continue once it's set up.";
// The retryable FACT is a promise, and an unrecognised cause cannot
// make it: if the cause the server added is a settled one, Continue
// will not re-run this cell. The dot is still the honest mark (it has
// not been shown to settle), but the sentence beside it must claim as
// little as the cause does.
const UNKNOWN_FACT = "This page is too old to say whether Continue will retry it.";

// The copy table for a TOOL's status code: what its door did, in user
// words, and the fix where one exists. Resolved by (tool, code) first,
// then by the base code every tool shares, then the generic line for a
// code this bundle has never heard of (a tool may add a mode
// server-side before this table learns it). `subject` is the tool as
// a user names it; `fix` renders only where it is the user's to do.
const TOOL_SUBJECT: Record<ToolKey, string> = { web_search: "Web search", find_contacts: "Finding contacts" };
const BASE_COPY: Record<string, { said: string; fix?: string }> = {
  not_configured: { said: "isn't set up on this deployment", fix: "Set it up under the agent's Tools." },
  rate_limited: { said: "was rate-limited past its retries" },
  unreachable: { said: "couldn't be reached" },
  error: { said: "kept failing" },
};
const TOOL_COPY: Record<ToolKey, Record<string, { said: string; fix?: string }>> = {
  web_search: {},
  find_contacts: {
    not_configured: { said: "isn't set up: it needs DataForSEO", fix: "Set it up under the agent's Tools." },
  },
};
const UNKNOWN_TOOL_COPY: { said: string; fix?: string } = { said: "reported a problem this page can't name" };
// The paid-door nudge is a tier-2 composition: the server ships WHICH
// door serves web search, and the sentence renders only where the
// paid door is a remedy (the free door refused). A contacts refusal
// already came from the paid door, and an unknown door claims
// nothing.
const PAID_DOOR_NUDGE = "DataForSEO (pay as you go, a deployment setting) gives dedicated throughput.";

/** The tools whose door did not serve this run, in the order the
 * config lists them (the toggle order the user sees). */
function degraded(tools: ToolStatuses): [ToolKey, string][] {
  return (Object.keys(TOOL_SUBJECT) as ToolKey[])
    .filter((tool) => tool in tools && tools[tool] !== "open")
    .map((tool) => [tool, tools[tool] as string]);
}

/** One tool's sentence for its status: "Web search couldn't be reached
 * on this row", plus the fix and the paid-door nudge where they apply. */
function toolSentence(tool: ToolKey, code: string, searchDoor: SearchDoor): { cause: string; fix: string } {
  const copy = TOOL_COPY[tool][code] ?? BASE_COPY[code] ?? UNKNOWN_TOOL_COPY;
  const nudge = tool === "web_search" && searchDoor === "duckduckgo" && code !== "not_configured" ? PAID_DOOR_NUDGE : "";
  return { cause: `${TOOL_SUBJECT[tool]} ${copy.said} on this row`, fix: [copy.fix ?? "", nudge].filter(Boolean).join(" ") };
}

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

const WarningDot = () => <span aria-hidden className="h-2 w-2 rounded-full bg-warning-edge" />;

/** The mark beside a FILLED value whose run had a degraded tool: the
 * answer landed on the evidence another tool found, and the user
 * should know which tool did not serve and what fixes it. */
export function DegradedToolMark({ tools, searchDoor = null }: { tools: ToolStatuses; searchDoor?: SearchDoor }) {
  const [first] = degraded(tools);
  if (!first) return null;
  const { cause, fix } = toolSentence(first[0], first[1], searchDoor);
  return (
    <CauseMark cause={cause} fact={fix || "The answer came from what the other tools found."}>
      <WarningDot />
    </CauseMark>
  );
}

/** Whether a states entry means "filled, and worth a mark": the server
 * ships a filled cell's state only when its run had a degraded tool. */
export function isDegradedFill(entry: RenderableCellStateWire | undefined): entry is RenderableCellStateWire {
  return entry !== undefined && entry.state === "filled" && degraded(entry.tools).length > 0;
}

/** One AI cell's non-value state: `pending` shimmers (the Skeleton
 * primitive already honors prefers-reduced-motion), a SETTLED blank
 * speaks its quiet word in the cell (data-quiet: faint and small,
 * never louder than a real value), a RETRYABLE blank keeps the
 * warning dot. Both open the fuller sentence plus their re-run fact
 * on tap, click, focus, and to screen readers (the row drawer will
 * carry the full diagnosis). A tool_* state takes its sentence from
 * the tool statuses beside it (which tool, what its door said). Clean
 * filled cells and not-attempted rows never reach here: both are the
 * ABSENCE of a state, rendered as the plain value or nothing.
 * `searchDoor` is the deployment's web-search door when the sheet has
 * it (fetched only once a degraded cell is on screen); the copy
 * composes the paid-door nudge off it. */
export function AiCellState({ entry, searchDoor = null }: { entry: RenderableCellStateWire; searchDoor?: SearchDoor }) {
  const state = entry.state as CellState | typeof UNKNOWN_CELL_STATE;
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
  if (state === "tool_not_configured" || state === "tool_unavailable" || state === "filled") {
    // `filled` here is a filled cell with no value left on the row
    // (cleared by hand after the fill): it reads as its tool statuses.
    const [first] = degraded(entry.tools);
    const sentence = first
      ? toolSentence(first[0], first[1], searchDoor)
      : { cause: "A tool did not serve this row", fix: "" };
    const fact = state === "tool_not_configured" ? NOT_CONFIGURED_FACT : RETRY_FACT;
    return (
      <CauseMark cause={sentence.cause} fact={[fact, sentence.fix].filter(Boolean).join(" ")}>
        <WarningDot />
      </CauseMark>
    );
  }
  return (
    <CauseMark
      cause={RETRYABLE_CAUSES[state as keyof typeof RETRYABLE_CAUSES]}
      fact={state === UNKNOWN_CELL_STATE ? UNKNOWN_FACT : RETRY_FACT}
    >
      <WarningDot />
    </CauseMark>
  );
}

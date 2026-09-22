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
  type ToolStatus,
  type ToolStatuses,
} from "@bower/api";

/** The deployment's web-search provider, when the sheet has fetched it
 * (a server fact the degraded-tool copy composes; null until known). */
export type SearchProviderChoice = AgentCatalog["search_provider"] | null;

// The blank causes in user words (the server ships the structured
// cause, this surface phrases it). The settled-vs-retryable PARTITION
// comes off the contract (SETTLED_CELL_STATES): a SETTLED cause is the
// model's own verdict, so the cell speaks a quiet word instead of a
// warning dot; a RETRYABLE cause is infrastructure's doing and keeps
// the dot. Every blank re-runs on the next fill whichever side it is
// on (the click is the consent to re-spend). The copy records below
// are typed against that partition, so a cause added server-side
// fails the build here instead of rendering an unnamed cell.
type SettledCause = SettledCellState;
type RetryableCause = Exclude<RenderableCellState, "pending" | "filled" | SettledCause>;

const SETTLED_CAUSES: Record<SettledCause, { word: string; cause: string }> = {
  no_evidence: { word: "none found", cause: "No evidence found" },
  no_answer: { word: "no answer", cause: "The model spent its search budget without answering" },
  unverified: { word: "unverified", cause: "What was found couldn't be confirmed for this row" },
  unparseable: { word: "unusable", cause: "The model's answer could not be used" },
  type_mismatch: { word: "wrong type", cause: "The answer did not fit this column's type" },
};
const SETTLED_FACT = "Runs again on Fill remaining; edit the prompt for a different ask.";
// A filled cell holds a value, and a fill writes only where blank, so
// no gesture short of deleting the column re-runs it: the fact states
// the exclusion and stops, naming no remedy.
const FILLED_FACT = "Won't re-run on Fill remaining: this cell already counted as filled.";

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
const RETRY_FACT = "Runs again on Fill remaining.";
const NOT_CONFIGURED_FACT = "Runs again on Fill remaining once it's set up.";
// A cause this bundle has never heard of claims nothing about what the
// next fill does with it either: the bundle cannot know, and a promise
// beside "older than the reason given" would contradict it.
const UNKNOWN_FACT = "This page is too old to say what the next fill does with it.";

// The copy table for a TOOL's status code: what its provider did, in user
// words, and the fix where one exists. Resolved by (tool, code) first,
// then by the base code every tool shares, then the generic line for a
// code this bundle has never heard of (a tool may add a mode
// server-side before this table learns it). `subject` is the tool as
// a user names it; `fix` renders only where it is the user's to do.
const TOOL_SUBJECT: Record<ToolKey, string> = { web_search: "Web search", find_contacts: "Finding contacts" };
// `rowScoped` marks the fragments that describe THIS ROW's weather;
// a deployment fact (not configured) or an unknowable one gets no
// "on this row", because the scope would assert what the code knows
// to be column-wide or cannot know at all. Required, not optional, so
// a code added later must state its scope to compile.
type ToolCopy = { said: string; fix?: string; rowScoped: boolean };
// The tables are typed CLOSED over the current contract (ToolStatus
// derives from TOOL_STATUSES), so a code added server-side fails
// this build until it gets copy; the LOOKUPS below stay open,
// because a deployed bundle must tolerate a code it has not heard
// of and degrade to the unknown line.
const BASE_COPY: Record<Exclude<ToolStatus, "open">, ToolCopy> = {
  not_configured: { said: "isn't set up on this deployment", fix: "Set it up under the agent's Tools.", rowScoped: false },
  rate_limited: { said: "was rate-limited past its retries", rowScoped: true },
  unreachable: { said: "couldn't be reached", rowScoped: true },
  error: { said: "kept failing", rowScoped: true },
};
const TOOL_COPY: Record<ToolKey, Partial<Record<ToolStatus, ToolCopy>>> = {
  web_search: {},
  find_contacts: {
    not_configured: {
      said: "isn't set up: it needs a metered search vendor",
      fix: "Set it up under the agent's Tools.",
      rowScoped: false,
    },
  },
};
const UNKNOWN_TOOL_COPY: ToolCopy = { said: "reported a problem this page can't name", rowScoped: false };

/** Whether any of a cell's tool statuses warrants fetching the
 * deployment's search provider: exactly the throughput codes that render
 * the paid-provider nudge below, exported so the fetch gate in sheet.tsx
 * and the copy that needs the door cannot drift. */
export function needsSearchProvider(tools: ToolStatuses): boolean {
  const status = tools.web_search ?? "open";
  return status === "rate_limited" || status === "unreachable";
}
// The paid-provider nudge is a tier-2 composition: the server ships WHICH
// door serves web search, and the sentence renders only where the
// paid door is a remedy (the free provider refused). A contacts refusal
// already came from the paid door, and an unknown door claims
// nothing.
const PAID_DOOR_NUDGE = "A metered search vendor (a deployment setting) gives dedicated throughput.";

/** The tools whose door did not serve this run, in the subject
 * table's order, which mirrors the server's AgentTool declaration
 * order (the order a blank's cause is named in). */
function degraded(tools: ToolStatuses): [ToolKey, string][] {
  return (Object.keys(TOOL_SUBJECT) as ToolKey[])
    .filter((tool) => tool in tools && tools[tool] !== "open")
    .map((tool) => [tool, tools[tool] as string]);
}

/** One tool's sentence for its status: "Web search couldn't be reached
 * on this row", plus the fix and the paid-provider nudge where they apply. */
function toolSentence(tool: ToolKey, code: string, searchProvider: SearchProviderChoice): { cause: string; fix: string } {
  const copy =
    (TOOL_COPY[tool] as Partial<Record<string, ToolCopy>>)[code] ??
    (BASE_COPY as Partial<Record<string, ToolCopy>>)[code] ??
    UNKNOWN_TOOL_COPY;
  // The nudge is a THROUGHPUT remedy, so it renders only where
  // throughput is the problem (the free provider refusing or timing
  // out), never on a door that is erroring or was never set up.
  const nudge =
    tool === "web_search" && searchProvider === "duckduckgo" && (code === "rate_limited" || code === "unreachable")
      ? PAID_DOOR_NUDGE
      : "";
  return {
    cause: `${TOOL_SUBJECT[tool]} ${copy.said}${copy.rowScoped ? " on this row" : ""}`,
    fix: [copy.fix ?? "", nudge].filter(Boolean).join(" "),
  };
}

/** Every pointer's path to a cell's why: the mark (word or dot) is a
 * real disclosure button, so tap, click, and Enter all open a small
 * panel speaking the cause and its re-run fact (hover cannot exist on
 * a coarse pointer, so hover-only tooltips leave mobile mute; title
 * stays as the desktop quick peek). The Popover primitive carries the
 * floor (aria-expanded, Escape, outside-click, focus return), and its
 * panel portals to body, so the sheet's own overflow never clips it. */
export function CauseMark({ cause, fact, children }: { cause: string; fact: string; children: React.ReactNode }) {
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
export function DegradedToolMark({ tools, searchProvider = null }: { tools: ToolStatuses; searchProvider?: SearchProviderChoice }) {
  const [first] = degraded(tools);
  if (!first) return null;
  const { cause, fix } = toolSentence(first[0], first[1], searchProvider);
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
 * `searchProvider` is the deployment's web-search provider when the sheet has
 * it (fetched only once a degraded cell is on screen); the copy
 * composes the paid-provider nudge off it. */
export function AiCellState({ entry, searchProvider = null }: { entry: RenderableCellStateWire; searchProvider?: SearchProviderChoice }) {
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
    // `filled` here is a filled cell with no value left on the row.
    // No shipped gesture produces one today (values are write-if-
    // blank, and column delete purges the record with the value), so
    // this is the defensive arm for a shape the wire can carry.
    const [first] = degraded(entry.tools);
    const sentence = first
      ? toolSentence(first[0], first[1], searchProvider)
      : { cause: "A tool did not serve this row", fix: "" };
    const fact =
      state === "tool_not_configured" ? NOT_CONFIGURED_FACT : state === "filled" ? FILLED_FACT : RETRY_FACT;
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

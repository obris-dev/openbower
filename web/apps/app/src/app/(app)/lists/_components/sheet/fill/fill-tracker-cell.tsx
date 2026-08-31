"use client";

import { useEffect, useState } from "react";
import { Button, cn, Popover, PopoverButton, PopoverPanel, Skeleton, Textarea } from "@bower/ui";
import {
  AGENT_PROMPT_MAX_LENGTH,
  getColumnPrompt,
  updateColumnPrompt,
  type ColumnFillSummary,
  type ColumnPromptWire,
  type ListColumn,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { FillProgress } from "./fill-progress";
import { RefillScope } from "./fill-refill-scope";
import { columnProgress, currentRunFor, trackerCell } from "./lib/fill-tracker";
import type { LiveRun } from "./lib/live-status";

// Below this, the prompt fits the clamp anyway and a toggle would be
// noise (binary; the clamp is three lines of a 24rem panel).
const PROMPT_TOGGLE_CHARS = 256;

/** One AI column's cell in the tracker row: the column's fill state
 * compactly ("164 filled | 13% run", the current run's filled count
 * beside its processed share; a live run draws a thin progress bar
 * under the text, a failed one tints danger), and the click-in
 * management surface behind it. The SERVER names the column's story
 * (the summary's current_fill_id and canonical filled count); this
 * cell renders it. One run per column is the invariant, so the
 * popover IS that column's management, one padded panel of fixed
 * width in reading order: the progress line, the FillProgress chip
 * while live (counters, ETA, pace, Stop), the failed run's error
 * verbatim with its resume verb when the newest run ended early
 * (Retry for a failed run, Continue for one the user stopped), the
 * scoped continue for terminal runs, and
 * the prompt peek with its inline EDIT (reading and writing the
 * column-scoped prompt endpoint, the column's CURRENT config, never a
 * run's frozen snapshot; a live run disables the affordance, since
 * the live run holds its snapshot and an edit only reaches the
 * NEXT run). The Popover primitive carries the disclosure floor
 * (aria-expanded, Escape, outside-click, focus return). `loaded` is
 * the typed loading discriminator (the first fills poll has not
 * answered): a loading cell renders a skeleton, held STATIC once the
 * page's trouble line speaks (pollTrouble), so it can never claim
 * progress a dead poll is not making. A loaded page ships one
 * summary per AI column, zero counts included, so a missing one on a
 * loaded page renders nothing; a column with no exposed run shows
 * the header line alone, naming the work. */
export function FillTrackerCell({
  listId,
  column,
  summary,
  loaded,
  pollTrouble,
  runs,
  rowCount,
  onStop,
  onRefill,
}: {
  listId: string;
  column: ListColumn;
  summary: ColumnFillSummary | undefined;
  loaded: boolean;
  pollTrouble: boolean;
  runs: LiveRun[];
  rowCount: number;
  onStop: (runId: string) => Promise<string | null>;
  onRefill: (columnKey: string, opts?: { rows?: number; resumeId?: string }) => Promise<string | null>;
}) {
  if (!loaded) {
    // Sized like the header line it resolves into. Once the page's
    // own trouble line speaks, the box holds STILL: a pulse beside
    // "updates aren't reaching this page" would claim a load the
    // dead poll is not making.
    return (
      <div className="flex h-5 items-center px-1">
        {pollTrouble ? <span aria-hidden className="h-3 w-16 rounded bg-wash-strong" /> : <Skeleton className="h-3 w-16" />}
        <span className="sr-only">
          {pollTrouble
            ? `The ${column.label} column's fill state is unavailable right now`
            : `Loading the ${column.label} column's fill state`}
        </span>
      </div>
    );
  }
  // A loaded page ships one summary per AI column; a missing one is a
  // contract gap and renders nothing rather than a state it cannot know.
  if (summary === undefined) return null;
  // The SUMMARY is what this cell needs; the run envelope only dresses
  // the chip. A column whose current run has aged off the fetched page
  // still shows its progress and keeps its management surface, rather
  // than vanishing as though the column had never been filled.
  const run = currentRunFor(summary, runs);
  const cell = trackerCell(run, summary.current_status);
  const live = cell.kind === "live";
  // Two instruments, one cell: is there WORK LEFT in this column (the
  // header, the question an operator actually has) over how the
  // current run is GOING (the status, which is where a quality number
  // belongs, beside the run it describes). The bar stays the run's walk.
  const progress = columnProgress(summary, rowCount);
  return (
    <Popover>
      <PopoverButton
        aria-label={`Manage the ${column.label} column's fill`}
        className="block w-full min-w-16 rounded px-1 py-0.5 text-left outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-signal"
      >
        {/* The header rides ABOVE the run status, so the two never say
            the same thing: with no fill attached there is no status to
            show and the header stands alone. */}
        <span
          className={cn(
            "block truncate tabular-nums",
            cell.kind === "none" ? "font-medium text-muted" : "text-[11px] text-faint",
          )}
        >
          {progress}
        </span>
        {cell.kind === "live" && (
          <span className="block truncate font-medium tabular-nums text-muted">{cell.text}</span>
        )}
        {cell.kind === "failed" && (
          // The word alone (authored beside its table in fill-tracker):
          // the popover carries the server's verbatim error, and a dead
          // run's counters are not replayed (the header's totals are
          // the honest answer).
          <span className="block truncate font-medium text-danger">{cell.text}</span>
        )}
        {live && (
          <span aria-hidden className="mt-1 block h-0.5 w-full overflow-hidden rounded-full bg-wash-strong">
            <span
              className="block h-full rounded-full bg-signal"
              style={{ width: `${Math.round(cell.fraction * 100)}%` }}
            />
          </span>
        )}
      </PopoverButton>
      {/* py-0 clears the primitive's own padding so p-4 below is the
          panel's ONE padding truth; the width sits on this container,
          not the content, so the panel holds steady as the peek swaps
          between read and edit. */}
      <PopoverPanel anchor="bottom start" className="py-0 motion-reduce:transition-none">
        <div className="w-[min(24rem,calc(100vw-2rem))] space-y-3 p-4">
          <p className="text-xs text-muted">Column: {progress}</p>
          {run !== null && <FillProgress run={run} onStop={() => onStop(run.id)} />}
          {!live && summary.current_status === "failed" && (
            // Tier 1: the server wrote the failed run's copy; render
            // it verbatim. The no-error fallback stays plain rather
            // than guessing a cause.
            <p className="text-xs text-danger">{summary.last_error?.message ?? "The fill failed."}</p>
          )}
          {!live && (summary.current_status === "failed" || summary.current_status === "cancelled") && (
            <ResumeContinue
              // The VERB tracks who ended the run: the user stopped a
              // cancelled one (picking it back up is Continue), the
              // system killed a failed one (Retry, beside the error
              // that says why). Same resume either way, and never a
              // "Rerun": the gesture finishes the remainder, it does
              // not re-run rows that answered.
              verb={summary.current_status === "failed" ? "Retry" : "Continue"}
              onContinue={() => onRefill(column.key, { resumeId: summary.current_fill_id })}
            />
          )}
          {!live && <RefillScope onRefill={(rows) => onRefill(column.key, { rows })} />}
          <PromptPeek listId={listId} columnKey={column.key} live={live} />
        </div>
      </PopoverPanel>
    </Popover>
  );
}

/** The resume verb for a stopped or failed newest run: resumes the
 * run the column names (the server judges what that run still owes
 * across every column it maps). A refusal renders verbatim beside the
 * verb (tier 1). */
function ResumeContinue({ verb, onContinue }: { verb: "Retry" | "Continue"; onContinue: () => Promise<string | null> }) {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState("");
  async function go() {
    if (busy) return;
    setBusy(true);
    setRefusal("");
    const message = await onContinue();
    // Success stays BUSY: the summary this button reads is stale
    // until the next poll lands (the branch then re-renders without
    // it), and re-arming now invites a second click that resends the
    // old resume id and buys a fill_active 409. Only a refusal
    // re-arms, with its verbatim why beside the verb.
    if (message === null) return;
    setBusy(false);
    setRefusal(message);
  }
  return (
    <div>
      <Button size="sm" variant="ghost" loading={busy} onClick={() => void go()}>
        {verb}
      </Button>
      {refusal && <p className="mt-1 text-xs text-danger">{refusal}</p>}
    </div>
  );
}

/** The peek at what fills this column: the column's CURRENT config
 * from the column-scoped prompt endpoint (never a run's frozen
 * snapshot, which is what a PAST run ran), the prompt under a
 * few-line clamp with an expand toggle, the model address beneath,
 * plus the inline EDIT: a plain bounded textarea with Save/Cancel
 * (the drawer's full editor is overkill here), Save calling the same
 * endpoint, a refusal rendered verbatim (tier 1). While the fill is
 * LIVE the affordance disables: the live run holds its frozen
 * snapshot, so an edit mid-walk would only invite mixed-config
 * confusion; stopping first keeps one run one config. Mounted per
 * popover open, so each open re-reads the current truth. */
function PromptPeek({ listId, columnKey, live }: { listId: string; columnKey: string; live: boolean }) {
  const [config, setConfig] = useState<ColumnPromptWire | null>(null);
  const [unreadable, setUnreadable] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [edited, setEdited] = useState(false);
  const [refusal, setRefusal] = useState("");

  useEffect(() => {
    let stale = false;
    async function read() {
      setUnreadable(false);
      const res = await getColumnPrompt(listId, columnKey);
      if (stale) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status === "ok") {
        setConfig(res.data);
        return;
      }
      // A skeleton that never resolves claims to be loading; say what
      // happened and offer the retry instead.
      setUnreadable(true);
    }
    void read();
    return () => {
      stale = true;
    };
  }, [listId, columnKey, attempt]);

  function beginEdit() {
    if (config === null) return;
    setDraft(config.prompt);
    setRefusal("");
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setRefusal("");
  }

  async function save() {
    if (saving) return;
    setSaving(true);
    setRefusal("");
    const res = await updateColumnPrompt(listId, columnKey, draft);
    setSaving(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status !== "ok") {
      // Tier 1: the server wrote the detail; render it verbatim.
      setRefusal(res.message);
      return;
    }
    setEditing(false);
    setConfig(res.data);
    setEdited(true);
  }

  // Why Edit is unavailable, or null when it is available.
  // AVAILABILITY and EXPLANATION are separate questions. Editing needs
  // a config and a stopped fill, full stop. What this control says
  // about it is narrower: the panel below already carries an
  // unreadable config with its own retry, so repeating it here would
  // be a second voice, and claiming a load in progress there would be
  // false. `disabled` must not derive from the copy: a state with
  // nothing to say here is still a state where editing is impossible.
  const editUnavailable = live || config === null;
  const editBlocked = live ? "Stop the fill to edit" : config === null && !unreadable ? "Loading the prompt" : null;
  const editBlockedId = `${columnKey}-edit-blocked`;

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-faint">Prompt</p>
        {!editing && (
          // The reason is TEXT beside the control, not a title on it:
          // browsers suppress pointer events on a disabled button and
          // drop it from the tab order, so a title there reaches
          // nobody, by hover or keyboard or screen reader. The button
          // stays visible so the affordance is still discoverable, and
          // aria-describedby ties the cause to it.
          <span className="flex min-w-0 items-center gap-2">
            {editBlocked !== null && (
              <span id={editBlockedId} className="truncate text-xs text-faint">
                {editBlocked}
              </span>
            )}
            <button
              type="button"
              disabled={editUnavailable}
              aria-describedby={editBlocked !== null ? editBlockedId : undefined}
              onClick={beginEdit}
              className="shrink-0 rounded text-xs text-faint underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-signal disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:no-underline"
            >
              Edit
            </button>
          </span>
        )}
      </div>
      {editing ? (
        <div className="mt-1">
          <Textarea
            autoFocus
            aria-label="Fill prompt"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                // The cancel is the editor's, not the popover's.
                event.preventDefault();
                event.stopPropagation();
                cancelEdit();
              }
            }}
            maxLength={AGENT_PROMPT_MAX_LENGTH}
            rows={6}
            className="font-mono text-xs"
          />
          <div className="mt-2 flex items-center gap-2">
            <Button size="sm" loading={saving} disabled={draft.trim() === ""} onClick={() => void save()}>
              Save
            </Button>
            <Button size="sm" variant="ghost" disabled={saving} onClick={cancelEdit}>
              Cancel
            </Button>
          </div>
          {refusal && <p className="mt-1.5 text-xs text-danger">{refusal}</p>}
        </div>
      ) : (
        <div className="mt-1 rounded-md bg-wash p-2">
          {unreadable ? (
            <div className="space-y-1.5">
              <p className="text-xs text-muted">This column&apos;s prompt could not be loaded.</p>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="rounded text-xs text-faint underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-signal"
              >
                Try again
              </button>
            </div>
          ) : config === null ? (
            <Skeleton className="h-4 w-3/4" />
          ) : (
            <>
              <p className={cn("whitespace-pre-wrap text-xs text-muted", !expanded && "line-clamp-3")}>
                {config.prompt}
              </p>
              {(config.prompt.length > PROMPT_TOGGLE_CHARS || config.prompt.split("\n").length > 3) && (
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setExpanded((cur) => !cur)}
                  className="mt-0.5 rounded text-xs text-faint underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-signal"
                >
                  {expanded ? "Show less" : "Show more"}
                </button>
              )}
              <p className="mt-1.5 text-xs text-faint">
                {config.model} via {config.source}
              </p>
            </>
          )}
        </div>
      )}
      {edited && !editing && (
        <p className="mt-1.5 text-xs text-faint">
          Blanks settled under the old prompt will run again on the next fill; use Fill next rows
          or Fill all remaining to start it.
        </p>
      )}
    </div>
  );
}

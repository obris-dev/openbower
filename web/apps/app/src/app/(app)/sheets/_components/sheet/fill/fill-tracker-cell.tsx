"use client";

import { useEffect, useState } from "react";
import { cn, Popover, PopoverButton, PopoverClose, PopoverPanel, Skeleton } from "@bower/ui";
import { getColumnAgent, type AiColumn, type ColumnAgentWire, type ColumnFillSummary } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { FillProgress } from "./fill-progress";
import { FillColumnScope } from "./fill-column-scope";
import { columnProgress, currentRunFor, trackerCell } from "./lib/fill-tracker";
import type { LiveRun } from "./lib/live-status";
import { EDIT_PROMPT_VERB } from "../use-ai/copy";

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
 * verbatim when the newest run failed (a stopped or failed run is
 * over: a new fill, from the Fill next / Fill all remaining controls
 * below, runs the rows it never reached), those controls for terminal
 * runs on an entry action's column, and
 * the prompt peek (the column's agent as a fill reads it, with Edit
 * prompt handing off to the column's edit drawer). The Popover
 * primitive carries the disclosure floor
 * (aria-expanded, Escape, outside-click, focus return). A MISSING
 * summary means the poll has not answered for this column yet: the
 * first page pending, or a just-added column whose entry arrives on
 * the next tick (a server that ships `columns` sends one per AI
 * column, zero counts included; the tolerant read's one gap is an
 * ancient server omitting the field, which leaves these cells
 * loading). It renders a
 * skeleton, held STATIC once the page's trouble line speaks
 * (pollTrouble), so it can never claim progress a dead poll is not
 * making. A column with no exposed run shows the header line alone,
 * naming the work. */
export function FillTrackerCell({
  sheetId,
  column,
  summary,
  pollTrouble,
  runs,
  rowCount,
  startsFill,
  onStop,
  onFill,
  onEdit,
}: {
  sheetId: string;
  column: AiColumn;
  summary: ColumnFillSummary | undefined;
  pollTrouble: boolean;
  runs: LiveRun[];
  rowCount: number;
  startsFill: boolean;
  onStop: (runId: string) => Promise<string | null>;
  onFill: (columnKey: string, opts?: { maxRowCount?: number }) => Promise<string | null>;
  onEdit: () => void;
}) {
  if (summary === undefined) {
    // Sized like the header line it resolves into. Once the page's
    // own trouble line speaks, the box holds STILL (the primitive's
    // resting look, nothing else changing): a pulse beside "updates
    // aren't reaching this page" would claim a load the dead poll is
    // not making.
    return (
      <div className="flex h-5 items-center px-1">
        {/* One owner of the look: the still box is the primitive
            minus its pulse (cn is twMerge-backed, so animate-none
            wins), never a hand-copied twin that drifts. */}
        {pollTrouble ? <Skeleton className="h-3 w-16 animate-none" /> : <Skeleton className="h-3 w-16" />}
        <span className="sr-only">
          {pollTrouble
            ? `The ${column.label} column's fill state is unavailable right now`
            : `Loading the ${column.label} column's fill state`}
        </span>
      </div>
    );
  }
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
        // The floor keeps a click target on a column with nothing to
        // report (its button would otherwise collapse to its padding).
        className="block min-h-4 w-full min-w-16 rounded px-1 py-0.5 text-left outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-signal"
      >
        {/* The header rides ABOVE the run status, so the two never say
            the same thing: with no fill attached there is no status to
            show and the header stands alone. A settled column says
            nothing at all; the column name is the whole header. */}
        {progress !== null && (
          <span
            className={cn(
              "block truncate tabular-nums",
              cell.kind === "none" ? "font-medium text-muted" : "text-[11px] text-faint",
            )}
          >
            {progress}
          </span>
        )}
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
          {progress !== null && <p className="text-xs text-muted">Column: {progress}</p>}
          {run !== null && <FillProgress run={run} onStop={() => onStop(run.id)} />}
          {!live && summary.current_status === "failed" && (
            // Tier 1: the server wrote the failed run's copy; render
            // it verbatim. The no-error fallback stays plain rather
            // than guessing a cause.
            <p className="text-xs text-danger">{summary.last_error?.message ?? "The fill failed."}</p>
          )}
          {!live &&
            (startsFill ? (
              <FillColumnScope onFill={(maxRowCount) => onFill(column.key, { maxRowCount })} />
            ) : (
              <p className="text-xs text-muted">Fills after the columns it waits on.</p>
            ))}
          <PromptPeek sheetId={sheetId} columnKey={column.key} onEdit={onEdit} />
        </div>
      </PopoverPanel>
    </Popover>
  );
}

/** The peek at what fills this column: the agent the column reads
 * live (what the next row runs under), the prompt under a few-line
 * clamp with an expand toggle, the model address beneath, and the way
 * to change it: Edit prompt opens the column's agent in the drawer
 * (read-only there for a saved agent, which the builder edits).
 * Mounted per popover open, so each open re-reads the current truth. */
function PromptPeek({ sheetId, columnKey, onEdit }: { sheetId: string; columnKey: string; onEdit: () => void }) {
  const [agent, setAgent] = useState<ColumnAgentWire | null>(null);
  const [unreadable, setUnreadable] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let stale = false;
    async function read() {
      setUnreadable(false);
      const res = await getColumnAgent(sheetId, columnKey);
      if (stale) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status === "ok") {
        setAgent(res.data);
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
  }, [sheetId, columnKey, attempt]);

  const prompt = agent?.config.prompt ?? "";
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-faint">Prompt</p>
        <PopoverClose
          type="button"
          onClick={onEdit}
          className="shrink-0 rounded text-xs text-faint underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-signal"
        >
          {EDIT_PROMPT_VERB}
        </PopoverClose>
      </div>
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
        ) : agent === null ? (
          <Skeleton className="h-4 w-3/4" />
        ) : (
          <>
            <p className={cn("whitespace-pre-wrap text-xs text-muted", !expanded && "line-clamp-3")}>{prompt}</p>
            {(prompt.length > PROMPT_TOGGLE_CHARS || prompt.split("\n").length > 3) && (
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
              {agent.config.model} via {agent.config.source}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

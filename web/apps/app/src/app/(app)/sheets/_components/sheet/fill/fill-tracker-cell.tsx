"use client";

import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { cn, Skeleton } from "@bower/ui";
import { fetchAgentCatalog, type AiColumn, type ColumnFillSummary } from "@bower/api";

import { staleWarning } from "./lib/fill-staleness";
import { currentRunFor, trackerLine } from "./lib/fill-tracker";
import type { LiveRun } from "./lib/live-status";

// The staleness clock's resolution (binary, about half a minute): the
// copy speaks in minutes against the stale threshold, so finer ticks buy
// nothing. Render-time Date.now() is impure; the clock is state.
const STALENESS_TICK_MS = 32_768;
// The card's width (w-64) and its margin from the viewport's edge.
const CARD_WIDTH = 256;
const CARD_MARGIN = 8;

/** One AI column's cell in the tracker row: progress, and progress
 * only. A bar and the percent of the column filled across the sheet,
 * whichever lane did the work (a fill or an autofill); the bar runs in
 * the signal color while rows are in progress. Its card, shown on
 * hover, focus or tap, breaks the percent down (filled, in progress,
 * no answer, left to fill), and carries a failed fill's reason and a
 * live fill's quiet-worker warning. Starting and stopping a fill live
 * in the column's header (its Fill / Stop button and its menu), never
 * here. A MISSING summary means the poll has not answered for this
 * column yet: a skeleton, held STATIC once the page's trouble line
 * speaks (pollTrouble), so it never claims progress a dead poll is not
 * making. */
export function FillTrackerCell({
  column,
  summary,
  pollTrouble,
  runs,
  rowCount,
}: {
  column: AiColumn;
  summary: ColumnFillSummary | undefined;
  pollTrouble: boolean;
  runs: LiveRun[];
  rowCount: number;
}) {
  const cardId = useId();
  // Where the card opens: the button's rectangle while it is hovered,
  // focused or tapped, else null. The card renders in a portal at fixed
  // coordinates, because the grid clips what overflows it and a short
  // sheet would cut the card off.
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (anchor === null) return;
    // The coordinates are a snapshot: any scroll or resize closes it.
    const close = () => setAnchor(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [anchor]);
  if (summary === undefined) {
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
  const run = currentRunFor(summary, runs);
  const line = trackerLine(summary, run, rowCount);
  const working = line.inProgress > 0;
  const show = (target: HTMLElement) => setAnchor(target.getBoundingClientRect());
  const hide = () => setAnchor(null);
  const card =
    anchor === null
      ? null
      : createPortal(
          <div
            aria-hidden
            className="fixed z-50 w-64 space-y-1 rounded-lg border border-hairline bg-surface p-3 text-xs text-muted shadow-lg"
            style={{
              top: anchor.bottom + 4,
              left: Math.max(CARD_MARGIN, Math.min(anchor.left, window.innerWidth - CARD_WIDTH - CARD_MARGIN)),
            }}
          >
            {line.lines.map((text, index) => (
              <p key={text} className={index === 0 ? "font-medium text-foreground" : undefined}>
                {text}
              </p>
            ))}
            {line.failed && (
              // Tier 1: the server wrote the failed run's copy; render it
              // verbatim. The no-error fallback stays plain.
              <p className="text-danger">{summary.last_error?.message ?? "The fill failed."}</p>
            )}
            {run !== null && <QuietWorker run={run} />}
          </div>,
          document.body,
        );
  return (
    <>
      <button
        type="button"
        aria-label={`${column.label} column progress`}
        aria-describedby={cardId}
        // Hover and focus show the card, and a tap (no hover on touch)
        // does too; leaving, blurring (a tap elsewhere) or Escape
        // closes it. A click only ever shows: it follows the focus
        // that already opened the card.
        onMouseEnter={(event) => show(event.currentTarget)}
        onMouseLeave={hide}
        onFocus={(event) => show(event.currentTarget)}
        onBlur={hide}
        onClick={(event) => show(event.currentTarget)}
        onKeyDown={(event) => {
          if (event.key === "Escape") hide();
        }}
        className="flex w-full min-w-16 items-center gap-2 rounded px-1 py-0.5 text-left outline-none hover:bg-wash focus-visible:ring-2 focus-visible:ring-signal"
      >
        <span aria-hidden className="h-1.5 min-w-8 flex-1 overflow-hidden rounded-full bg-wash-strong">
          <span
            className={cn("block h-full rounded-full", line.failed ? "bg-danger" : working ? "bg-signal" : "bg-success")}
            style={{ width: `${line.percent}%` }}
          />
        </span>
        <span className={cn("shrink-0 tabular-nums", line.failed ? "font-medium text-danger" : "text-muted")}>
          {line.failed ? "Failed" : `${line.percent}%`}
        </span>
        {/* The card's facts for a screen reader, always present (the
            visible card mounts only while open). */}
        <span id={cardId} className="sr-only">
          {[...line.lines, ...(line.failed ? [summary.last_error?.message ?? "The fill failed."] : [])].join(". ")}
        </span>
      </button>
      {card}
    </>
  );
}

/** A live fill's staleness line, when its worker has gone quiet
 * (fill-staleness owns the judgment and the copy; a stale heartbeat is
 * degraded REPORTING, never failure). The not-started warning composes
 * the deployment's support_followup fragment, fetched only once that
 * warning is due. */
function QuietWorker({ run }: { run: LiveRun }) {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const refresh = () => setNow(Date.now());
    const seed = setTimeout(refresh, 0);
    const timer = setInterval(refresh, STALENESS_TICK_MS);
    return () => {
      clearTimeout(seed);
      clearInterval(timer);
    };
  }, [run]);

  const [supportFollowup, setSupportFollowup] = useState<string | null>(null);
  const needsFollowup = run.status === "pending" && staleWarning(run, now) !== null;
  useEffect(() => {
    if (!needsFollowup || supportFollowup !== null) return;
    let superseded = false;
    async function load() {
      const res = await fetchAgentCatalog();
      if (!superseded && res.status === "ok") setSupportFollowup(res.data.support_followup);
    }
    void load();
    return () => {
      superseded = true;
    };
  }, [needsFollowup, supportFollowup]);

  const warning = staleWarning(run, now, supportFollowup ?? undefined);
  return warning ? <p className="text-warning">{warning}</p> : null;
}

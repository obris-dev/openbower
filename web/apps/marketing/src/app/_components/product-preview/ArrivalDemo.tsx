"use client";

import { useEffect, useState } from "react";

import { GRID, filledClass } from "./cells";

// The arriving row (pushed in). Company is the pushed input; the three
// AI columns (contact, fundraise, headline) are what the engine
// researches on arrival, left to right.
const ARRIVING = ["vertex.io", "Nadia Okoye, VP Sales", "$25M Series B, Feb 2026", "Expanded into APAC"];

// Cycle clock (ms). A row reveals at the top, its three AI cells fill
// left to right (each shimmering while it researches), the highlight
// settles into the sheet, a hold, then the row exits and the next
// arrival begins.
const TICK_MS = 90;
const REVEAL_MS = 450;
const FILL_START = 950;
const COL_STAGGER = 750;
const SHIMMER_MS = 950;
const N_AI = 3;
const FILL_DONE = FILL_START + (N_AI - 1) * COL_STAGGER + SHIMMER_MS;
const SETTLE_MS = 900;
const HOLD_MS = 2600;
const CYCLE = FILL_DONE + SETTLE_MS + HOLD_MS;
const EXIT_MS = 500;
const EXIT_START = CYCLE - EXIT_MS;

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

function Shimmer({ col }: { col: number }) {
  const w = col === 0 ? "w-4/5" : col === 1 ? "w-3/5" : "w-5/6";
  return (
    <span className={`relative block h-3.5 overflow-hidden rounded bg-ink/10 dark:bg-paper/10 ${w}`} aria-hidden>
      <span className="ob-shimmer absolute inset-0" />
    </span>
  );
}

/** The animated top of the sheet: the title bar with its Processing
 * badge, the column headers, and the one row that arrives and fills.
 * Everything the clock drives lives here, so the settled rows below
 * stay server markup. Server render and reduced motion both show the
 * row finished. */
export function ArrivalDemo() {
  // null = not animating (SSR, pre-mount, reduced motion): the
  // finished row renders, so no-JS visitors get full content.
  const [elapsed, setElapsed] = useState<number | null>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const started = performance.now();
    const timer = window.setInterval(() => {
      setElapsed((performance.now() - started) % CYCLE);
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  // Arriving row: fade + slide entrance, a highlight that fades as the
  // fill settles, and an exit fade before the next arrival.
  const revealT = elapsed === null ? 1 : clamp01(elapsed / REVEAL_MS);
  const exitT = elapsed === null ? 0 : clamp01((elapsed - EXIT_START) / EXIT_MS);
  const rowOpacity = revealT * (1 - exitT);
  const rowShift = (1 - revealT) * -8;
  const highlightT = elapsed === null ? 0 : 1 - clamp01((elapsed - FILL_DONE) / SETTLE_MS);

  // The badge is an EVENT: present only while a cell is actually
  // filling, absent on the settled (and static) sheet.
  const badgeVisible = elapsed !== null && elapsed >= FILL_START && elapsed < FILL_DONE + 300;

  function aiCell(col: number): "empty" | "shimmer" | "filled" {
    if (elapsed === null) return "filled";
    const start = FILL_START + col * COL_STAGGER;
    if (elapsed < start) return "empty";
    if (elapsed < start + SHIMMER_MS) return "shimmer";
    return "filled";
  }

  return (
    <>
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3 dark:border-paper/10">
        <p className="text-sm font-semibold text-ink dark:text-paper">Q4 outbound list</p>
        <span
          aria-hidden={!badgeVisible}
          className={
            "flex items-center gap-1.5 rounded-md bg-signal/10 px-2 py-1 text-xs font-medium text-signal transition-opacity duration-300 motion-reduce:transition-none " +
            (badgeVisible ? "opacity-100" : "opacity-0")
          }
        >
          <svg className="h-3 w-3 animate-spin motion-reduce:animate-none" viewBox="0 0 24 24" fill="none" aria-hidden>
            <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
            <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
          </svg>
          Processing
        </span>
      </div>

      <div
        className={`${GRID} border-b border-ink/10 py-2 text-[11px] font-medium uppercase tracking-wide text-ink/40 dark:border-paper/10 dark:text-paper/40`}
      >
        <span>Company</span>
        <span>Decision maker</span>
        <span>Last fundraise</span>
        <span>Recent headlines</span>
      </div>

      <div
        className={`${GRID} border-b border-ink/5 py-2.5 text-sm dark:border-paper/5`}
        style={{
          opacity: rowOpacity,
          transform: `translateY(${rowShift}px)`,
          backgroundColor: `rgba(0, 183, 195, ${0.1 * highlightT})`,
          boxShadow: `inset 3px 0 0 0 rgba(0, 183, 195, ${highlightT})`,
        }}
      >
        <span className="flex min-w-0 items-center gap-1.5 font-medium text-signal">
          <span className="truncate">{ARRIVING[0]}</span>
          <span
            aria-hidden
            className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-signal/70"
            style={{ opacity: highlightT }}
          >
            just now
          </span>
        </span>
        {[1, 2, 3].map((dataIdx, col) => {
          const state = aiCell(col);
          return (
            <div key={col} className="min-w-0">
              {state === "empty" && <span aria-hidden>&nbsp;</span>}
              {state === "shimmer" && <Shimmer col={col} />}
              {state === "filled" && <span className={filledClass(col)}>{ARRIVING[dataIdx]}</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

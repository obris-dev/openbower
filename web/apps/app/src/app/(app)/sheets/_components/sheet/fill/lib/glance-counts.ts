import type { ColumnFillSummary } from "@bower/api";

import { isLiveStatus } from "./live-status.ts";

/** The slice of the summary these decisions read (ColumnFillSummary
 * satisfies it structurally). */
export type GlanceSummary = Pick<ColumnFillSummary, "column_key" | "current_fill_id" | "current_status">;

export type GlanceCounts = { filling: number; failed: number };

/** Pure counts and copy for the footer's fills glance: how many
 * COLUMNS are filling and how many carry a failed newest run. The
 * glance exists so neither fact can hide off the edge of a wide
 * sheet whose tracker cells have scrolled away; the per-column
 * detail stays in the tracker row, so the glance never repeats it.
 *
 * A column counts as FILLING when its current run is on the live
 * page OR its summary status is live. That is deliberately WIDER
 * than the tracker cell, which renders live off the join alone: in
 * the status-only window (the round trip after Stop, a fill
 * started between the two reads) the glance reports the server's last
 * word for the column while the cell, holding no envelope to draw
 * counters or a bar from, shows its header line alone. What the join
 * leg guarantees is the direction that would LIE: the column's own
 * run, while it is on the live page, can never read failed here
 * while the cell shows its walk. Both surfaces read the SAME pointer
 * (current_fill_id), so they cannot disagree about which run speaks
 * for a column; the join only prefers the runs leg's still-live
 * reading of that one run for a tick. */
export function glanceCounts(summaries: readonly GlanceSummary[], liveRunIds: ReadonlySet<string>): GlanceCounts {
  let filling = 0;
  let failed = 0;
  for (const summary of summaries) {
    if (liveRunIds.has(summary.current_fill_id) || isLiveStatus(summary.current_status)) filling += 1;
    else if (summary.current_status === "failed") failed += 1;
  }
  return { filling, failed };
}

export function fillingLabel(filling: number): string | null {
  if (filling === 0) return null;
  return filling === 1 ? "Filling 1 column" : `Filling ${filling} columns`;
}

export function failedLabel(failed: number): string | null {
  if (failed === 0) return null;
  return failed === 1 ? "1 column failed" : `${failed} columns failed`;
}

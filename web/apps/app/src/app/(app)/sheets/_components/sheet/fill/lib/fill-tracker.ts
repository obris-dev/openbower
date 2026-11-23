import type { ColumnFillSummary, FillRunWire } from "@bower/api";

/** Pure decisions for the tracker row (the per-column fill surface
 * pinned under the sheet's header): which live run speaks for a column
 * and what its cell says. The SERVER names every fact on the poll
 * (`summaries[].current_fill_id`, `current_status`, and the canonical
 * `filled`/`pending` totals): this module only renders them.
 *
 * The cell is a bar and a percent of the column filled across the
 * sheet, whichever lane did the work (a fill or an autofill); its card
 * (on hover, focus or tap) breaks that down. A failed fill marks the
 * cell. */

/** The slice of the run envelope these decisions read (FillRunWire
 * satisfies it structurally). */
export type TrackerRun = Pick<FillRunWire, "target_row_count"> & {
  counters: Pick<FillRunWire["counters"], "attempted">;
};

export type TrackerLine = {
  /** Filled over the sheet's rows, clamped to [0, 1]: the bar's width. */
  fraction: number;
  /** The same, as the whole percent the cell prints. */
  percent: number;
  /** Rows queued or running for this column, from any lane. */
  inProgress: number;
  /** The card's breakdown, one fact per line. */
  lines: string[];
  /** The column's newest fill failed and nothing is running now. */
  failed: boolean;
};

/** The live run that speaks for a column: the envelope behind the
 * server's `current_fill_id` pointer, or null when the newest run is
 * not on the live page (terminal, or nothing has run). */
export function currentRunFor<J extends { id: string }>(
  summary: ColumnFillSummary | undefined,
  runs: readonly J[],
): J | null {
  if (!summary || !summary.current_fill_id) return null;
  return runs.find((run) => run.id === summary.current_fill_id) ?? null;
}

function count(n: number): string {
  return n.toLocaleString("en-US");
}

/** The column's progress. In progress is the larger of the open runs
 * on the column's node (any lane, the summary's `pending`) and what a
 * live fill still has to walk: a fill queues its rows in slices, so
 * its open runs can undercount the rows it will reach. Left to fill is
 * what neither an attempt nor work in progress has reached. */
export function trackerLine(summary: ColumnFillSummary, run: TrackerRun | null, rowCount: number): TrackerLine {
  const unwalked = run === null ? 0 : Math.max(0, run.target_row_count - run.counters.attempted);
  const inProgress = Math.max(summary.pending, unwalked);
  const fraction = rowCount > 0 ? Math.min(Math.max(summary.filled / rowCount, 0), 1) : 0;
  const percent = Math.round(fraction * 100);
  const unanswered = Math.max(0, summary.attempted - summary.filled);
  const left = Math.max(0, rowCount - summary.attempted - inProgress);
  const lines = [`${count(summary.filled)} of ${count(rowCount)} rows filled (${percent}%)`];
  if (inProgress > 0) lines.push(`${count(inProgress)} in progress`);
  if (unanswered > 0) lines.push(`${count(unanswered)} ran with no answer`);
  if (left > 0) lines.push(`${count(left)} left to fill`);
  return { fraction, percent, inProgress, lines, failed: run === null && summary.current_status === "failed" };
}

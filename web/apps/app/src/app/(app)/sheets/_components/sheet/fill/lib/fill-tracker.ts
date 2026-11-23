import type { ColumnFillSummary, FillRunWire } from "@bower/api";

/** Pure decisions for the tracker row (the per-column fill surface
 * pinned under the sheet's header): which live run speaks for a column
 * and what its cell says. The SERVER names every fact on the poll
 * (`summaries[].current_fill_id`, `current_status`, and the canonical
 * `filled`/`pending` totals): this module only renders them.
 *
 * The cell is one line, whichever lane did the work (a fill or an
 * autofill): how much of the column is filled across the sheet, and
 * how many rows are in progress while any are, with a thin bar for
 * that time. A failed fill adds "Fill failed" beneath. */

/** The slice of the run envelope these decisions read (FillRunWire
 * satisfies it structurally). */
export type TrackerRun = Pick<FillRunWire, "target_row_count"> & {
  counters: Pick<FillRunWire["counters"], "attempted">;
};

export type TrackerLine = {
  /** "164 of 200 filled", plus " | 3 in progress" while rows are. */
  text: string;
  /** Rows queued or running for this column, from any lane. */
  inProgress: number;
  /** Filled over the sheet's rows, clamped to [0, 1]: the bar's width. */
  fraction: number;
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

/** The column's one line. In progress is the larger of the open runs
 * on the column's node (any lane, the summary's `pending`) and what a
 * live fill still has to walk: a fill queues its rows in slices, so
 * its open runs can undercount the rows it will reach. */
export function trackerLine(summary: ColumnFillSummary, run: TrackerRun | null, rowCount: number): TrackerLine {
  const unwalked = run === null ? 0 : Math.max(0, run.target_row_count - run.counters.attempted);
  const inProgress = Math.max(summary.pending, unwalked);
  const filled = `${count(summary.filled)} of ${count(rowCount)} filled`;
  return {
    text: inProgress > 0 ? `${filled} | ${count(inProgress)} in progress` : filled,
    inProgress,
    fraction: rowCount > 0 ? Math.min(Math.max(summary.filled / rowCount, 0), 1) : 0,
    failed: run === null && summary.current_status === "failed",
  };
}

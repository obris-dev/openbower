import type { ColumnFillSummary, FillRunWire } from "@bower/api";

/** Pure decisions for the tracker row (the per-column fill surface
 * pinned under the sheet's header): which live run speaks for a column
 * and what its cell says. The SERVER names every fact on the poll
 * (`summaries[].current_fill_id`, `current_status`, and the canonical
 * `filled`/`attempted` totals): this module only renders them, never
 * reconstructs them from a page of runs.
 *
 * The cell state table the tracker renders from (the page ships LIVE
 * runs only; a terminal story arrives as the summary's status):
 * | joined run, else summary   | kind   | cell                                       |
 * | none, or ended quietly     | none   | the header line alone                      |
 * | a run joins (it IS live)   | live   | "164 filled | 13% run" + thin progress bar |
 * | failed                     | failed | "Fill failed"; the popover speaks the error|
 * A finished run's counters are not replayed: the header's historic
 * totals are the honest answer once a run has ended. */

/** The slice of the run envelope these decisions read (FillRunWire
 * satisfies it structurally). */
export type TrackerRun = Pick<FillRunWire, "target_row_count"> & {
  counters: Pick<FillRunWire["counters"], "filled" | "attempted">;
};

export type TrackerCell =
  | { kind: "none" }
  | { kind: "failed"; text: string }
  | {
      kind: "live";
      /** "164 filled | 13% run": the current run's filled count beside
       * its PROCESSED share (attempted over targeted; progress means
       * processed, not productive, so a blank-heavy walk still reads
       * as moving). */
      text: string;
      /** The run's walk progress (attempted over targeted, clamped to
       * [0, 1]); the live bar's width, and the percent's source. */
      fraction: number;
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

/** The column header's one line, and only while there is something to
 * say: the cells being worked on right now (an open run of the column's
 * node, whichever lane queued it), else the rows no attempt has reached,
 * else nothing. A settled column has nothing to report: its cells say
 * what landed, and a count of the past standing where a status goes
 * reads as a status. The exact target of a fill is computed on the
 * consent path, where it is spent and where it has to be right; this
 * line claims only what is known. */
export function columnProgress(summary: ColumnFillSummary, rowCount: number): string | null {
  if (summary.pending > 0) return `filling ${count(summary.pending)} ${summary.pending === 1 ? "row" : "rows"}`;
  const owed = Math.max(0, rowCount - summary.attempted - summary.pending);
  if (owed > 0) return `${count(owed)} ${owed === 1 ? "row" : "rows"} to fill`;
  return null;
}

/** A joined run IS live (the page ships nothing else), so its cell is
 * the walk; without one, the summary's status decides whether the
 * cell carries a failure or just the header line. */
export function trackerCell(run: TrackerRun | null, currentStatus: ColumnFillSummary["current_status"]): TrackerCell {
  if (run !== null) {
    const target = run.target_row_count;
    const fraction = target > 0 ? Math.min(Math.max(run.counters.attempted / target, 0), 1) : 0;
    // The percent derives from the clamped fraction, so the text and
    // the bar can never disagree.
    const pct = Math.round(fraction * 100);
    return { kind: "live", text: `${count(run.counters.filled)} filled | ${pct}% run`, fraction };
  }
  // "Fill failed", not "Run failed": copy a user reads keeps the
  // feature's word; "run" stays a code and wire noun.
  return currentStatus === "failed" ? { kind: "failed", text: "Fill failed" } : { kind: "none" };
}

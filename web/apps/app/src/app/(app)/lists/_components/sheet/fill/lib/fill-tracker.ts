import type { ColumnFillSummary, FillRunWire } from "@bower/api";

/** Pure decisions for the tracker row (the per-column fill surface
 * pinned under the sheet's header): which run speaks for a column and
 * what its cell says. The SERVER names both facts on the runs poll
 * (`summaries[].current_fill_id` and `filled`, computed from the
 * canonical cell records): this module only renders them, never
 * reconstructs them from a page of runs.
 *
 * The cell state table the tracker renders from:
 * | current run for the column | kind   | cell                                     |
 * | none                       | none   | quiet blank                              |
 * | pending or running         | live   | "164 filled | 13% run" + thin progress bar |
 * | failed                     | failed | the counts, danger-tinted                |
 * | complete or cancelled      | done   | the counts, quiet                        |
 */

/** The slice of the run envelope these decisions read (FillRunWire
 * satisfies it structurally). */
export type TrackerRun = Pick<FillRunWire, "status" | "confirmed_row_count"> & {
  counters: Pick<FillRunWire["counters"], "filled" | "attempted">;
};

export type TrackerCell =
  | { kind: "none" }
  | {
      kind: "live" | "done" | "failed";
      /** "164 filled | 13% run": the current run's filled count beside
       * its PROCESSED share (attempted over confirmed; progress means
       * processed, not productive, so a blank-heavy walk still reads
       * as moving). */
      text: string;
      /** The run's walk progress (attempted over confirmed, clamped to
       * [0, 1]); the live bar's width, and the percent's source. */
      fraction: number;
    };

/** The run that speaks for a column: the envelope behind the server's
 * `current_fill_id` pointer, or null when none is exposed. */
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

/** THE COLUMN HEADER: how much of the sheet this column has been run
 * on.
 *
 * Attempted-versus-remaining, because that is the question an operator
 * has about a column: is there work left here. Filled-of-attempted is
 * a QUALITY number (how well did the model do), and it belongs in the
 * run status underneath, next to the run it describes.
 *
 * A column with nothing run names the WORK, because there the sheet
 * count and the target are the same question. Once a run has
 * happened they are not: a refill also re-runs rows that failed on
 * infrastructure and skips rows whose prompt variables are all blank,
 * so a subtraction from the sheet total is a different number from
 * the one the button beneath it would spend. So the mixed case
 * reports only what is KNOWN, run out of the sheet, and names no
 * remainder at all. The exact target is computed on the consent path,
 * where it is spent and where it has to be right.
 */
export function columnProgress(summary: ColumnFillSummary, rowCount: number): string {
  const remaining = Math.max(0, rowCount - summary.attempted);
  if (summary.attempted === 0) return `${count(rowCount)} rows to fill`;
  if (remaining === 0) return `all ${count(rowCount)} rows run`;
  return `${count(summary.attempted)} of ${count(rowCount)} rows run`;
}

export function trackerCell(run: TrackerRun | null): TrackerCell {
  if (run === null) return { kind: "none" };
  const confirmed = run.confirmed_row_count;
  const fraction = confirmed > 0 ? Math.min(Math.max(run.counters.attempted / confirmed, 0), 1) : 0;
  // The percent derives from the clamped fraction, so the text and the
  // bar can never disagree.
  const pct = Math.round(fraction * 100);
  const kind = run.status === "pending" || run.status === "running" ? "live" : run.status === "failed" ? "failed" : "done";
  return { kind, text: `${count(run.counters.filled)} filled | ${pct}% run`, fraction };
}

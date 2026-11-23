import type { AiColumn } from "@bower/api";

import { currentRunFor, startsFill, trackerLine, type ColumnFill } from "../fill";
import type { SheetFills } from "./tracker-row";

/** One AI column's fill controls, from the sheet's fill state: its
 * live run (the server's pointer joined to the live page) and whether
 * it starts fills itself. */
export function columnFill(column: AiColumn, fills: SheetFills): ColumnFill {
  const summary = fills.summaries.find((entry) => entry.column_key === column.key);
  const run = currentRunFor(summary, fills.runs);
  // Before the poll's first answer nothing is known to be left.
  const left = summary === undefined ? 0 : trackerLine(summary, run, fills.rowCount).left;
  return {
    runId: run?.id ?? null,
    canFill: startsFill(column, fills.entryActionIds),
    left,
    fill: (maxRowCount) => fills.onFill(column.key, { maxRowCount }),
    stop: fills.onStop,
  };
}

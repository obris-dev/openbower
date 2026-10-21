"use client";

import type { ColumnFillSummary, ListColumn } from "@bower/api";

import { FillTrackerCell, startsFill, type LiveRun } from "../fill";
import { GutterCell } from "./gutter-cell";
import { HEADER_HEIGHT, TRAILING_CELL_WIDTH } from "./lib/column-sizing";
import type { GridHeader } from "./use-grid-table";

/** The tracker row's inputs, one object because they only travel
 * together: the LIVE runs and the management verbs the popover's
 * chip and continues call. */
export type SheetFills = {
  // The sheet's own id: the popover's prompt edit calls the
  // column-scoped endpoint.
  listId: string;
  runs: LiveRun[];
  // Server truth per column (current run and its status, the newest
  // failure, canonical totals).
  summaries: ColumnFillSummary[];
  // The page's poll cannot reach the server (the footer's line): the
  // loading cells hold still instead of claiming progress.
  pollTrouble: boolean;
  rowCount: number;
  // The nodes a fill may start at (the sheet's ListDetail entry actions): a column
  // behind a barrier offers no fill of its own.
  entryActionIds: string[];
  onStop: (runId: string) => Promise<string | null>;
  onFill: (columnKey: string, opts?: { maxRowCount?: number }) => Promise<string | null>;
};

/** The band under the header, stuck to it so it scrolls horizontally
 * WITH the columns it describes: one compact fill-state cell per AI
 * column (the click-in management popover behind it), empty cells
 * elsewhere. The header's fixed height is what makes this band's
 * sticky offset exact. */
export function TrackerRow({ headers, fills }: { headers: GridHeader[]; fills: SheetFills }) {
  return (
    <div
      // A floor on the height: a column with nothing to report renders
      // an empty cell, and the band still has to read as a band.
      className="sticky z-20 flex min-h-6 w-max min-w-full border-b border-hairline bg-surface text-xs"
      style={{ top: HEADER_HEIGHT }}
    >
      <GutterCell className="z-30" />
      {headers.map((header) => {
        const column: ListColumn = header.column.columnDef.meta!.column;
        return (
          <div
            key={header.id}
            className="flex min-w-0 flex-none items-center border-r border-hairline px-2 py-1"
            style={{ width: header.getSize() }}
          >
            {column.kind === "ai" && (
              <FillTrackerCell
                listId={fills.listId}
                column={column}
                summary={fills.summaries.find((entry) => entry.column_key === column.key)}
                pollTrouble={fills.pollTrouble}
                runs={fills.runs}
                rowCount={fills.rowCount}
                startsFill={startsFill(column, fills.entryActionIds)}
                onStop={fills.onStop}
                onFill={fills.onFill}
              />
            )}
          </div>
        );
      })}
      <div className="flex-none border-r border-hairline" style={{ width: TRAILING_CELL_WIDTH }} />
    </div>
  );
}

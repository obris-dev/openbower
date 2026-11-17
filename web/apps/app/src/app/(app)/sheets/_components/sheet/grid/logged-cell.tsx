import type { ReactNode } from "react";
import { LocalTime, TIME_FORMAT, cn } from "@bower/ui";

import { COLUMN_TYPE_META } from "../../../../_components/column-type-meta";
import { LOGGED_WIDTH } from "./lib/column-sizing";

// A log's own fact about each row, not a cell: when the row arrived. It
// sits beside the gutter in every band, outside the columns, so cell
// navigation, widths and the clipboard never see it; a press on it is
// the row's, as on the gutter. It reads as a datetime column does: that
// type's glyph and the same moment format.
export const LOGGED_LABEL = "Logged";
const LoggedIcon = COLUMN_TYPE_META.datetime.icon;

function LoggedFrame({ className, rowId, children }: { className?: string; rowId?: string; children?: ReactNode }) {
  return (
    <div
      data-row-id={rowId}
      className={cn("flex flex-none items-center gap-1.5 border-b border-r border-hairline p-2 text-sm", className)}
      style={{ width: LOGGED_WIDTH }}
    >
      {children}
    </div>
  );
}

/** The header's Logged cell, dressed as a datetime column's head. */
export function LoggedHeader() {
  return (
    <LoggedFrame className="font-medium text-foreground">
      <LoggedIcon aria-hidden className="h-4 w-4 shrink-0 text-muted" />
      <span className="min-w-0 truncate">{LOGGED_LABEL}</span>
    </LoggedFrame>
  );
}

/** The tracker band's empty cell under Logged. */
export function LoggedTrackerCell() {
  return <LoggedFrame />;
}

/** One row's Logged time, in the reader's own zone. */
export function LoggedCell({ rowId, at }: { rowId: string; at: string }) {
  return (
    <LoggedFrame rowId={rowId} className="whitespace-nowrap tabular-nums text-foreground">
      <LocalTime iso={at} format={TIME_FORMAT.STAMP} className="min-w-0 truncate" />
    </LoggedFrame>
  );
}

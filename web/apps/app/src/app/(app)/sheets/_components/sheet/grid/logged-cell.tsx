import type { ReactNode } from "react";
import { CalendarClock } from "lucide-react";
import { LocalTime, TIME_FORMAT, cn } from "@bower/ui";

import { LOGGED_WIDTH } from "./lib/column-sizing";

// A log's own fact about each row, not a cell: when the row arrived. It
// sits beside the gutter in every band, outside the columns, so cell
// navigation, widths and the clipboard never see it; a press on it is
// the row's, as on the gutter.
export const LOGGED_LABEL = "Logged";

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

/** The header's Logged cell, dressed as a column's head: a date and time,
 * so the date-time glyph beside the label. */
export function LoggedHeader() {
  return (
    <LoggedFrame className="font-medium text-foreground">
      <CalendarClock aria-hidden className="h-4 w-4 shrink-0 text-muted" />
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
      <LocalTime iso={at} format={TIME_FORMAT.MINUTE} />
    </LoggedFrame>
  );
}

import type { ReactNode } from "react";
import { cn } from "@bower/ui";

import { GUTTER_WIDTH } from "./lib/column-sizing";

/** The leading cell every band shares: sticky to the left edge so the
 * row number (or the header's mark) stays put while the columns
 * scroll under it. Its background is opaque for the same reason, or
 * the cells would show through as they pass. */
export function GutterCell({ className, children }: { className?: string; children?: ReactNode }) {
  return (
    <div
      className={cn(
        "sticky left-0 z-10 flex flex-none items-center justify-center border-r border-hairline bg-surface text-xs text-faint",
        className,
      )}
      style={{ width: GUTTER_WIDTH }}
    >
      {children}
    </div>
  );
}

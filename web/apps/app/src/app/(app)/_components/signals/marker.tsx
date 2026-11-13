import { Radio } from "lucide-react";
import { cn } from "@bower/ui";

import { SIGNAL_MARKER } from "./copy";

/** The marker on a sheet a signal fills, wherever the sheet is named: its
 * words as the tooltip and the glyph's name. The colour is the caller's
 * (quiet in a list, the signal's own beside a sheet's title). */
export function SignalMarker({ className }: { className?: string }) {
  return (
    <span title={SIGNAL_MARKER} className={cn("inline-flex shrink-0", className)}>
      <Radio aria-label={SIGNAL_MARKER} className="h-4 w-4" />
    </span>
  );
}

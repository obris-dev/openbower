import type { ConfidenceLevel } from "@bower/api";
import { cn } from "@bower/ui";

import { CONFIDENCE_BARS, levelWord, litBars } from "./lib/confidence";

/** How sure the model was, as a five-segment scale and the level's word:
 * lit segments read at a glance and never by colour alone, and the
 * unlit ones keep the track's shape so the scale reads as one thing. */
export function ConfidenceMark({ level }: { level: ConfidenceLevel }) {
  const lit = litBars(level);
  return (
    <span className="flex items-center gap-2">
      <span aria-hidden className="flex items-center gap-0.5">
        {Array.from({ length: CONFIDENCE_BARS }, (_, index) => (
          <span
            key={index}
            className={cn("h-1.5 w-3 rounded-[2px]", index < lit ? "bg-foreground" : "bg-wash-strong")}
          />
        ))}
      </span>
      <span className="text-muted">{levelWord(level)}</span>
    </span>
  );
}

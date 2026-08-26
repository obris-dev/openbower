"use client";

import { Check, X } from "lucide-react";

import { goToSection, type ChecklistItem } from "./lib/readiness";

/** What a blocked action still needs, each item a jump to its section.
 * Shared by the agent builder's footer and the sheet's AI drawer:
 * both keep their action ENABLED and diagnose on the attempt, so both
 * need the same signpost row at the action. */
export function ReadinessChecklist({ items }: { items: ChecklistItem[] }) {
  return (
    // Full-width and CENTERED on mobile (its own composed row above
    // the actions); inline left on desktop.
    <div
      role="group"
      className="flex w-full flex-wrap items-center justify-center gap-3 text-xs sm:w-auto sm:justify-start"
      aria-label="What this action still needs"
    >
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => goToSection(item.anchor)}
          className={
            item.missing
              ? "inline-flex items-center gap-1 text-warning hover:underline"
              : "inline-flex items-center gap-1 text-muted"
          }
        >
          {item.missing ? <X aria-hidden className="h-3 w-3" /> : <Check aria-hidden className="h-3 w-3" />}
          {item.label}
          {item.missing && <span className="sr-only"> (missing)</span>}
        </button>
      ))}
    </div>
  );
}
